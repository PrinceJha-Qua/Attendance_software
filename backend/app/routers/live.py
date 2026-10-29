"""Professor live console (phase 7): close + attendance records, live list, Attention Panel, spot checks, resolve, manual add, CSV export, WebSocket feed."""
import asyncio
import csv
import io
import random
import statistics
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import attention, config
from app.db import SessionLocal, get_db
from app.deps import require_role
from app.events import hub
from app.models import AttendanceRecord, Checkin, ClassSession, Course, RosterEntry, Spotcheck, User
from app.routers.sessions import _out, _own_session
from app.security import decode_token
from app.storage import audit

router = APIRouter(tags=["prof-session"])
prof_only = require_role("professor")


def _roster(db: Session, s: ClassSession) -> list[User]:
    return list(db.scalars(select(User).join(RosterEntry, RosterEntry.student_id == User.id).where(RosterEntry.course_id == s.course_id).order_by(User.roll_no, User.name)))


def _rows(db: Session, s: ClassSession) -> list[dict]:
    """One row per rostered student: what is known now, and what the record would be if the session closed now."""
    cks = {c.student_id: c for c in db.scalars(select(Checkin).where(Checkin.session_id == s.id))}
    recs = {r.student_id: r for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == s.id))}
    rows = []
    for u in _roster(db, s):
        c, r = cks.get(u.id), recs.get(u.id)
        if r:
            display, final, source, note = r.status, r.status, r.source, r.note
        elif c:
            display, final, source, note = ("review" if c.status == "REVIEW" else "checked_in"), "present", "checkin", None  # unresolved REVIEW counts present
        else:
            display, final, source, note = "not_here", "absent", "default", None
        rows.append({"student_id": u.id, "name": u.name, "roll_no": u.roll_no, "display": display, "final": final, "source": source, "note": note,
                     "checkin": {"status": c.status, "score": c.score, "flags": c.flags, "latency_ms": c.latency_ms, "at": c.created_at.isoformat()} if c else None})
    return rows


def _set_record(db: Session, s: ClassSession, student_id: str, status: str, source: str, note: str | None) -> AttendanceRecord:
    r = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == s.id, AttendanceRecord.student_id == student_id))
    if r:
        r.status, r.source, r.note = status, source, note
    else:
        r = AttendanceRecord(session_id=s.id, student_id=student_id, status=status, source=source, note=note)
        db.add(r)
    return r


def _on_roster(db: Session, s: ClassSession, student_id: str) -> User:
    u = db.get(User, student_id)
    if not u or not db.scalar(select(RosterEntry.id).where(RosterEntry.course_id == s.course_id, RosterEntry.student_id == student_id)):
        raise HTTPException(404, "NOT_ON_ROSTER")
    return u


def _cleared(s: ClassSession, student_id: str) -> None:
    hub.publish(s.id, "attention.cleared", {"student_id": student_id})


@router.post("/sessions/{session_id}/close")
def close(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    """Writes one attendance record per rostered student. Closing twice is a no-op. Unresolved REVIEW counts as present."""
    s = _own_session(db, session_id, prof)
    if s.status == "created":
        raise HTTPException(409, "INVALID_STATE")
    if s.status != "closed":
        for row in _rows(db, s):
            if row["source"] in ("checkin", "default"):
                flags = (row["checkin"] or {}).get("flags", [])
                unreviewed = bool(row["checkin"]) and row["checkin"]["status"] == "REVIEW"
                _set_record(db, s, row["student_id"], row["final"], row["source"], f"unreviewed: {','.join(flags)}" if unreviewed else None)
        s.status, s.closed_at = "closed", datetime.now(timezone.utc)
        audit(db, prof.id, "session.closed", s.id)
        try:
            db.commit()
        except IntegrityError:  # a concurrent close wrote the records first
            db.rollback()
            s = _own_session(db, session_id, prof)
        hub.publish(s.id, "session.state", {"status": "closed"})
    return _out(db, s)


@router.get("/sessions/{session_id}/attendance")
def attendance(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    s = _own_session(db, session_id, prof)
    rows = _rows(db, s)
    count = lambda d: sum(r["display"] == d for r in rows)  # noqa: E731
    audits = list(db.scalars(select(Spotcheck).where(Spotcheck.session_id == s.id, Spotcheck.was_audit_sample.is_(True))))
    return {
        "session": _out(db, s),
        "summary": {"roster": len(rows), "present": sum(r["final"] == "present" for r in rows), "checked_in": count("checked_in"), "review": count("review"),
                    "not_here": count("not_here"), "resolved_present": count("present"), "resolved_absent": count("absent"), "excused": count("excused"),
                    "audit_sampled": len(audits), "audit_not_seen": sum(a.result == "not_seen" for a in audits)},
        "students": rows,
    }


@router.get("/sessions/{session_id}/attention")
def attention_feed(session_id: str, k: int = Query(default=5, ge=1, le=20), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    s = _own_session(db, session_id, prof)
    cards, total = attention.items(db, s, prof, k)
    return {"items": cards, "total_open": total}


@router.get("/sessions/{session_id}/spotcheck/suggestions")
def suggestions(session_id: str, k: int = Query(default=5, ge=1, le=20), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    s = _own_session(db, session_id, prof)
    cards, total = attention.items(db, s, prof, k, audit_n=config.AUDIT_SAMPLE)
    return {"items": cards, "total_open": total}


class SpotIn(BaseModel):
    result: Literal["seen", "not_seen", "unsure"]
    note: str | None = Field(default=None, max_length=300)


@router.post("/sessions/{session_id}/spotcheck/{student_id}")
def spotcheck(session_id: str, student_id: str, body: SpotIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    """The professor's verdict after calling the name. seen -> present, not_seen -> absent (both final but appealable), unsure -> no change."""
    s = _own_session(db, session_id, prof)
    _on_roster(db, s, student_id)
    if s.status not in ("live", "paused"):
        raise HTTPException(409, "INVALID_STATE")
    c = db.scalar(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id == student_id))
    audit_sample = bool(c and c.status == "VERIFIED")
    db.add(Spotcheck(session_id=s.id, student_id=student_id, result=body.result, was_audit_sample=audit_sample))
    if body.result != "unsure":
        _set_record(db, s, student_id, "present" if body.result == "seen" else "absent", "resolved", body.note or f"spot-check: {body.result.replace('_', ' ')}")
    audit(db, prof.id, f"spotcheck.{body.result}", student_id)
    db.commit()
    _cleared(s, student_id)
    return {"student_id": student_id, "result": body.result}


class ResolveIn(BaseModel):
    status: Literal["present", "absent"]
    note: str | None = Field(default=None, max_length=300)


@router.post("/sessions/{session_id}/attendance/{student_id}/resolve")
def resolve(session_id: str, student_id: str, body: ResolveIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    s = _own_session(db, session_id, prof)
    _on_roster(db, s, student_id)
    if s.status == "created":
        raise HTTPException(409, "INVALID_STATE")
    _set_record(db, s, student_id, body.status, "resolved", body.note)
    audit(db, prof.id, f"attendance.resolved.{body.status}", student_id)
    db.commit()
    _cleared(s, student_id)
    return {"student_id": student_id, "status": body.status, "source": "resolved"}


class ManualIn(BaseModel):
    student_id: str
    reason: str = Field(min_length=3, max_length=300)
    status: Literal["present"] = "present"


@router.post("/sessions/{session_id}/attendance/manual", status_code=201)
def manual(session_id: str, body: ManualIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    """For a student who could not use the app (no phone, consent refused). A reason is mandatory and audited."""
    s = _own_session(db, session_id, prof)
    _on_roster(db, s, body.student_id)
    if s.status == "created":
        raise HTTPException(409, "INVALID_STATE")
    _set_record(db, s, body.student_id, body.status, "manual", body.reason)
    audit(db, prof.id, "attendance.manual", body.student_id)
    db.commit()
    _cleared(s, body.student_id)
    return {"student_id": body.student_id, "status": body.status, "source": "manual"}


def _cell(v) -> str:
    t = "" if v is None else str(v)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t  # spreadsheet formula injection guard


@router.get("/sessions/{session_id}/export")
def export(session_id: str, format: str = Query(default="csv"), prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    if format != "csv":
        raise HTTPException(422, "UNSUPPORTED_FORMAT")
    s = _own_session(db, session_id, prof)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["roll_no", "name", "status", "source", "checkin_status", "score", "flags", "note"])
    for r in _rows(db, s):
        c = r["checkin"]
        w.writerow([_cell(x) for x in (r["roll_no"], r["name"], r["final"], r["source"], c["status"] if c else "", c["score"] if c else "", " ".join(c["flags"]) if c else "", r["note"])])
    audit(db, prof.id, "session.exported", s.id)
    db.commit()
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="attendance_{s.id}.csv"', "X-Session-Status": s.status})


# ---- live feed ----
def _tick(sid: str) -> dict:
    with SessionLocal() as db:
        s = db.get(ClassSession, sid)
        roster = db.scalar(select(func.count()).select_from(RosterEntry).where(RosterEntry.course_id == s.course_id)) or 0
        rows = db.execute(select(Checkin.status, Checkin.latency_ms).where(Checkin.session_id == sid)).all()
    lat = sorted(x[1] for x in rows if x[1] is not None)
    q = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None  # noqa: E731
    return {"status": s.status, "roster": roster, "checked_in": len(rows), "review": sum(x[0] == "REVIEW" for x in rows),
            "rate_per_s": round(hub.recent_checkins(sid, 10) / 10, 2), "p50_ms": q(0.5), "p95_ms": q(0.95)}


def _decorate(ev: dict, prof_id: str) -> dict:
    """Attention events carry a fresh signed photo URL minted for this professor."""
    if ev["type"] == "attention.raised" and ev["data"].get("has_photo"):
        from app.storage import photo_url
        return {**ev, "data": {**ev["data"], "photo_url": photo_url(ev["data"]["student_id"], prof_id)}}
    return ev


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(ws: WebSocket, session_id: str, token: str = "", since_seq: int = 0):
    claims = decode_token(token, "access") if token else None
    with SessionLocal() as db:
        user = db.get(User, claims["sub"]) if claims else None
        s = db.get(ClassSession, session_id)
        course = db.get(Course, s.course_id) if s else None
        allowed = bool(user and user.role == "professor" and course and course.professor_id == user.id)
    if not allowed:
        await ws.close(code=4401 if not user or user.role != "professor" else 4404)
        return
    await ws.accept()
    q, backlog, cur, resync = hub.subscribe(session_id, since_seq)

    async def reader():
        try:
            while True:
                if (await ws.receive_text()).strip().lower() in ("ping", '{"type":"ping"}'):
                    q.put_nowait({"seq": 0, "type": "pong", "data": {}, "t": 0})
        except WebSocketDisconnect:
            q.put_nowait(None)
        except Exception:
            q.put_nowait(None)

    rtask = asyncio.create_task(reader())
    try:
        await ws.send_json({"seq": cur, "type": "hello", "data": {"resync": resync, **_tick(session_id)}, "t": 0})
        for ev in backlog:
            await ws.send_json(_decorate(ev, user.id))
        last_tick = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
                if ev is None:
                    break
                await ws.send_json(_decorate(ev, user.id))
            except asyncio.TimeoutError:
                pass
            if loop.time() - last_tick >= 1.0:
                last_tick = loop.time()
                await ws.send_json({"seq": 0, "type": "stats.tick", "data": await asyncio.to_thread(_tick, session_id), "t": 0})
    except Exception:
        pass
    finally:
        rtask.cancel()
        hub.unsubscribe(session_id, q)
