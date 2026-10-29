"""Student attendance dashboard + disputes, professor course report + disputes inbox (phase 8)."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import calculator
from app.db import get_db
from app.deps import require_role
from app.models import AttendanceRecord, ClassSession, Course, Dispute, RosterEntry, User
from app.storage import audit

router = APIRouter()
student_only = require_role("student")
prof_only = require_role("professor")


def _counts(recs: list[AttendanceRecord], course: Course) -> tuple[int, int]:
    attended = sum(r.status == "present" or (r.status == "excused" and course.excused_counts) for r in recs)
    return attended, len(recs)


def _summary(recs: list[AttendanceRecord], course: Course) -> dict:
    a, t = _counts(recs, course)
    return calculator.compute(a, t, course.required_pct, course.planned_total_sessions)


def _recs(db: Session, course_id: str, student_id: str) -> list[tuple[AttendanceRecord, ClassSession]]:
    q = (select(AttendanceRecord, ClassSession).join(ClassSession, ClassSession.id == AttendanceRecord.session_id)
         .where(ClassSession.course_id == course_id, AttendanceRecord.student_id == student_id, ClassSession.status == "closed")
         .order_by(ClassSession.closed_at.desc(), ClassSession.id))
    return [(r, s) for r, s in db.execute(q)]


def _when(s: ClassSession) -> str | None:
    d: datetime | None = s.started_at or s.closed_at
    return d.isoformat() if d else None


# ---- student ----
@router.get("/students/me/courses", tags=["student-dashboard"])
def my_courses(student: User = Depends(student_only), db: Session = Depends(get_db)):
    courses = db.scalars(select(Course).join(RosterEntry, RosterEntry.course_id == Course.id).where(RosterEntry.student_id == student.id).order_by(Course.name))
    out = []
    for c in courses:
        recs = [r for r, _ in _recs(db, c.id, student.id)]
        out.append({"course_id": c.id, "name": c.name, "professor": db.get(User, c.professor_id).name, **_summary(recs, c)})
    return out


@router.get("/students/me/courses/{course_id}/attendance", tags=["student-dashboard"])
def my_course_attendance(course_id: str, student: User = Depends(student_only), db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if not c or not db.scalar(select(RosterEntry.id).where(RosterEntry.course_id == c.id, RosterEntry.student_id == student.id)):
        raise HTTPException(404, "COURSE_NOT_FOUND")
    pairs = _recs(db, c.id, student.id)
    disputes = {d.record_id: d for d in db.scalars(select(Dispute).where(Dispute.student_id == student.id, Dispute.record_id.in_([r.id for r, _ in pairs] or [""])))}
    hist = [{"record_id": r.id, "session_id": s.id, "date": _when(s), "status": r.status, "source": r.source, "note": r.note if r.source != "checkin" else None,
             "dispute": {"id": disputes[r.id].id, "status": disputes[r.id].status} if r.id in disputes else None,
             "can_dispute": r.status == "absent" and r.id not in disputes} for r, s in pairs]
    return {"course": {"course_id": c.id, "name": c.name}, "summary": _summary([r for r, _ in pairs], c), "history": hist}


class DisputeIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/students/me/attendance/{record_id}/dispute", status_code=201, tags=["student-dashboard"])
def create_dispute(record_id: str, body: DisputeIn, student: User = Depends(student_only), db: Session = Depends(get_db)):
    r = db.get(AttendanceRecord, record_id)
    if not r or r.student_id != student.id:
        raise HTTPException(404, "RECORD_NOT_FOUND")  # never reveals other students' records
    if r.status != "absent":
        raise HTTPException(409, "NOT_DISPUTABLE")
    if db.scalar(select(Dispute.id).where(Dispute.record_id == r.id)):
        raise HTTPException(409, "DISPUTE_EXISTS")  # one dispute per record, decided or not
    d = Dispute(record_id=r.id, student_id=student.id, note=body.note.strip())
    db.add(d)
    audit(db, student.id, "dispute.created", r.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "DISPUTE_EXISTS")
    return {"id": d.id, "status": d.status}


@router.get("/students/me/disputes", tags=["student-dashboard"])
def my_disputes(student: User = Depends(student_only), db: Session = Depends(get_db)):
    q = (select(Dispute, AttendanceRecord, ClassSession, Course).join(AttendanceRecord, AttendanceRecord.id == Dispute.record_id)
         .join(ClassSession, ClassSession.id == AttendanceRecord.session_id).join(Course, Course.id == ClassSession.course_id)
         .where(Dispute.student_id == student.id).order_by(Dispute.created_at.desc()))
    return [{"id": d.id, "course": c.name, "date": _when(s), "note": d.note, "status": d.status, "decided_note": d.decided_note, "record_status": r.status} for d, r, s, c in db.execute(q)]


# ---- professor ----
def _own_course(db: Session, course_id: str, prof: User) -> Course:
    c = db.get(Course, course_id)
    if not c or c.professor_id != prof.id:
        raise HTTPException(404, "COURSE_NOT_FOUND")
    return c


@router.get("/courses/{course_id}/report", tags=["prof-reports"])
def course_report(course_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    c = _own_course(db, course_id, prof)
    sessions = list(db.scalars(select(ClassSession).where(ClassSession.course_id == c.id)))
    closed = [s for s in sessions if s.status == "closed"]
    open_ids = [s.id for s in sessions if s.status != "closed"]
    recs: dict[str, list[AttendanceRecord]] = {}
    for r in db.scalars(select(AttendanceRecord).join(ClassSession, ClassSession.id == AttendanceRecord.session_id).where(ClassSession.course_id == c.id, ClassSession.status == "closed")):
        recs.setdefault(r.student_id, []).append(r)
    open_disputes: dict[str, int] = {}
    for d in db.scalars(select(Dispute).join(AttendanceRecord, AttendanceRecord.id == Dispute.record_id).join(ClassSession, ClassSession.id == AttendanceRecord.session_id).where(ClassSession.course_id == c.id, Dispute.status == "open")):
        open_disputes[d.student_id] = open_disputes.get(d.student_id, 0) + 1
    students = []
    for u in db.scalars(select(User).join(RosterEntry, RosterEntry.student_id == User.id).where(RosterEntry.course_id == c.id).order_by(User.roll_no, User.name)):
        rs = recs.get(u.id, [])
        students.append({"student_id": u.id, "roll_no": u.roll_no, "name": u.name, **_summary(rs, c),
                         "unreviewed_flags": sum(1 for r in rs if r.source == "checkin" and (r.note or "").startswith("unreviewed")), "open_disputes": open_disputes.get(u.id, 0)})
    return {"course": {"course_id": c.id, "name": c.name, "required_pct": c.required_pct, "planned_total": c.planned_total_sessions},
            "sessions_conducted": len(closed), "open_session_id": open_ids[0] if open_ids else None, "students": students}


@router.get("/courses/{course_id}/disputes", tags=["prof-reports"])
def course_disputes(course_id: str, status: Literal["open", "all"] = Query(default="open"), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    c = _own_course(db, course_id, prof)
    q = (select(Dispute, AttendanceRecord, ClassSession, User).join(AttendanceRecord, AttendanceRecord.id == Dispute.record_id)
         .join(ClassSession, ClassSession.id == AttendanceRecord.session_id).join(User, User.id == Dispute.student_id)
         .where(ClassSession.course_id == c.id).order_by(Dispute.created_at.desc()))
    if status == "open":
        q = q.where(Dispute.status == "open")
    return [{"id": d.id, "student": {"student_id": u.id, "name": u.name, "roll_no": u.roll_no}, "date": _when(s), "session_id": s.id, "record_status": r.status,
             "record_source": r.source, "record_note": r.note, "note": d.note, "status": d.status, "decided_note": d.decided_note} for d, r, s, u in db.execute(q)]


class DecideIn(BaseModel):
    decision: Literal["accept", "reject"]
    note: str | None = Field(default=None, max_length=300)


@router.post("/disputes/{dispute_id}/decide", tags=["prof-reports"])
def decide(dispute_id: str, body: DecideIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    d = db.get(Dispute, dispute_id)
    r = db.get(AttendanceRecord, d.record_id) if d else None
    s = db.get(ClassSession, r.session_id) if r else None
    c = db.get(Course, s.course_id) if s else None
    if not c or c.professor_id != prof.id:
        raise HTTPException(404, "DISPUTE_NOT_FOUND")
    new = "accepted" if body.decision == "accept" else "rejected"
    if d.status != "open":
        if d.status == new:  # same decision again: idempotent
            return {"id": d.id, "status": d.status, "record_status": r.status}
        raise HTTPException(409, "ALREADY_DECIDED")
    d.status, d.decided_note = new, body.note
    if new == "accepted":
        r.status, r.source, r.note = "present", "resolved", f"dispute accepted{': ' + body.note if body.note else ''}"
    audit(db, prof.id, f"dispute.{new}", d.id)
    db.commit()
    return {"id": d.id, "status": d.status, "record_status": r.status}
