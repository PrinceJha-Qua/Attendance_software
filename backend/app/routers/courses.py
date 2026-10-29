"""Professor setup: courses, roster CSV, enrollment confirmation, signed photos (phase 2)."""
import csv
import io
import secrets
import string
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.deps import require_role
from app.models import Course, FaceTemplate, RosterEntry, User
from app.security import hash_password
from app.storage import audit, decrypt, photo_sig_ok, photo_url, sniff_image
from datetime import datetime, timezone

router = APIRouter(tags=["prof-setup"])
prof_only = require_role("professor")


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    planned_total_sessions: int | None = Field(default=None, ge=1, le=1000)
    required_pct: float = Field(default=75.0, ge=1, le=100)
    excused_counts: bool = True


class CoursePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    planned_total_sessions: int | None = Field(default=None, ge=1, le=1000)
    required_pct: float | None = Field(default=None, ge=1, le=100)
    excused_counts: bool | None = None


def _own_course(db: Session, course_id: str, prof: User) -> Course:
    c = db.get(Course, course_id)
    if not c or c.professor_id != prof.id:
        raise HTTPException(404, "COURSE_NOT_FOUND")
    return c


def _course_out(db: Session, c: Course) -> dict:
    roster = db.scalar(select(func.count()).select_from(RosterEntry).where(RosterEntry.course_id == c.id)) or 0
    confirmed = db.scalar(
        select(func.count()).select_from(RosterEntry).join(FaceTemplate, FaceTemplate.student_id == RosterEntry.student_id)
        .where(RosterEntry.course_id == c.id, FaceTemplate.status == "confirmed")
    ) or 0
    return {
        "id": c.id, "name": c.name, "planned_total_sessions": c.planned_total_sessions,
        "required_pct": c.required_pct, "excused_counts": c.excused_counts,
        "roster_count": roster, "confirmed_count": confirmed,
    }


@router.get("/courses")
def list_courses(prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return [_course_out(db, c) for c in db.scalars(select(Course).where(Course.professor_id == prof.id).order_by(Course.name))]


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    c = Course(professor_id=prof.id, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, prof.id, "course.created", c.id)
    db.commit()
    return _course_out(db, c)


@router.patch("/courses/{course_id}")
def patch_course(course_id: str, body: CoursePatch, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    c = _own_course(db, course_id, prof)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None and k != "planned_total_sessions":
            continue
        setattr(c, k, v)
    audit(db, prof.id, "course.updated", c.id)
    db.commit()
    return _course_out(db, c)


def _gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


@router.post("/courses/{course_id}/roster/import")
async def import_roster(course_id: str, file: UploadFile = File(...), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    course = _own_course(db, course_id, prof)
    raw = await file.read(config.MAX_CSV_BYTES + 1)
    if len(raw) > config.MAX_CSV_BYTES:
        raise HTTPException(413, "CSV_TOO_LARGE")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "BAD_CSV")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    if not {"roll_no", "name", "email"} <= set(headers):
        raise HTTPException(422, "CSV_MISSING_COLUMNS")
    rows = list(reader)
    if len(rows) > config.MAX_CSV_ROWS:
        raise HTTPException(413, "CSV_TOO_MANY_ROWS")

    on_roster = set(db.scalars(select(RosterEntry.student_id).where(RosterEntry.course_id == course.id)))
    added, already, created, errors = 0, 0, [], []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        roll = (row.get(headers["roll_no"]) or "").strip()
        name = (row.get(headers["name"]) or "").strip()
        email = (row.get(headers["email"]) or "").strip().lower()
        if not roll or not name or "@" not in email:
            errors.append({"row": i, "reason": "MISSING_OR_INVALID_FIELD"})
            continue
        if email in seen or roll in seen:
            errors.append({"row": i, "reason": "DUPLICATE_IN_FILE"})
            continue
        seen.update((email, roll))
        by_email = db.scalar(select(User).where(User.email == email))
        by_roll = db.scalar(select(User).where(User.roll_no == roll))
        if by_email and by_roll and by_email.id != by_roll.id:
            errors.append({"row": i, "reason": "EMAIL_ROLL_CONFLICT"})
            continue
        user = by_email or by_roll
        if user and user.role != "student":
            errors.append({"row": i, "reason": "NOT_A_STUDENT_ACCOUNT"})
            continue
        if not user:
            pw = _gen_password()
            user = User(email=email, roll_no=roll, name=name, role="student", password_hash=hash_password(pw))
            db.add(user)
            db.flush()
            created.append({"roll_no": roll, "email": email, "initial_password": pw})
        if user.id in on_roster:
            already += 1
        else:
            db.add(RosterEntry(course_id=course.id, student_id=user.id))
            on_roster.add(user.id)
            added += 1
    audit(db, prof.id, "roster.import", f"{course.id}:added={added}")
    db.commit()
    return {"added_to_roster": added, "already_on_roster": already, "created_accounts": created, "errors": errors}


@router.get("/courses/{course_id}/roster")
def get_roster(course_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    _own_course(db, course_id, prof)
    q = (
        select(User, FaceTemplate.status)
        .join(RosterEntry, RosterEntry.student_id == User.id)
        .outerjoin(FaceTemplate, FaceTemplate.student_id == User.id)
        .where(RosterEntry.course_id == course_id)
        .order_by(User.roll_no)
    )
    return [{"student_id": u.id, "name": u.name, "roll_no": u.roll_no, "email": u.email, "enrollment": st or "none"} for u, st in db.execute(q)]


@router.get("/courses/{course_id}/enrollments/pending")
def pending_enrollments(course_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    _own_course(db, course_id, prof)
    q = (
        select(User)
        .join(RosterEntry, RosterEntry.student_id == User.id)
        .join(FaceTemplate, FaceTemplate.student_id == User.id)
        .where(RosterEntry.course_id == course_id, FaceTemplate.status == "pending")
        .order_by(User.roll_no)
    )
    return [{"student_id": u.id, "name": u.name, "roll_no": u.roll_no, "photo_url": photo_url(u.id, prof.id)} for u in db.scalars(q)]


def _decide(db: Session, course_id: str, student_id: str, prof: User, status: str):
    _own_course(db, course_id, prof)
    if not db.scalar(select(RosterEntry).where(RosterEntry.course_id == course_id, RosterEntry.student_id == student_id)):
        raise HTTPException(404, "STUDENT_NOT_ON_ROSTER")
    tpl = db.get(FaceTemplate, student_id)
    if not tpl or tpl.status != "pending":
        raise HTTPException(409, "NOT_PENDING")
    tpl.status = status
    tpl.confirmed_by = prof.id
    tpl.confirmed_at = datetime.now(timezone.utc)
    audit(db, prof.id, f"enroll.{status}", student_id)
    db.commit()
    return {"status": status}


@router.post("/courses/{course_id}/enrollments/{student_id}/confirm")
def confirm(course_id: str, student_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _decide(db, course_id, student_id, prof, "confirmed")


@router.post("/courses/{course_id}/enrollments/{student_id}/reject")
def reject(course_id: str, student_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _decide(db, course_id, student_id, prof, "rejected")


@router.get("/students/{student_id}/photo", tags=["ops"])
def get_photo(student_id: str, exp: int = Query(...), sig: str = Query(...), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    if not photo_sig_ok(student_id, prof.id, exp, sig):
        raise HTTPException(403, "BAD_OR_EXPIRED_LINK")
    shares_course = db.scalar(
        select(RosterEntry.id).join(Course, Course.id == RosterEntry.course_id)
        .where(RosterEntry.student_id == student_id, Course.professor_id == prof.id).limit(1)
    )
    tpl = db.get(FaceTemplate, student_id)
    if not shares_course or not tpl or not tpl.photo_enc:
        raise HTTPException(404, "PHOTO_NOT_FOUND")
    data = decrypt(tpl.photo_enc)
    audit(db, prof.id, "photo.viewed", student_id)
    db.commit()
    return Response(data, media_type=sniff_image(data) or "application/octet-stream", headers={"Cache-Control": "no-store"})
