"""Session lifecycle (except close, phase 7) and rolling QR token batches (phase 3)."""
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config, tokens
from app.db import get_db
from app.deps import require_role
from app.models import Checkin, ClassSession, Course, RosterEntry, User
from app.events import hub
from app.storage import audit

router = APIRouter(tags=["prof-session"])
prof_only = require_role("professor")


class SessionIn(BaseModel):
    course_id: str
    step_ms: int = Field(default=1500, ge=1000, le=10000)
    audio_mode: str = Field(default="off", pattern="^(off|burst)$")


def _own_session(db: Session, session_id: str, prof: User) -> ClassSession:
    s = db.get(ClassSession, session_id)
    course = db.get(Course, s.course_id) if s else None
    if not s or not course or course.professor_id != prof.id:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    return s


def _out(db: Session, s: ClassSession) -> dict:
    roster = db.scalar(select(func.count()).select_from(RosterEntry).where(RosterEntry.course_id == s.course_id)) or 0
    checked_in = db.scalar(select(func.count()).select_from(Checkin).where(Checkin.session_id == s.id, Checkin.status != "REJECTED")) or 0
    return {
        "id": s.id, "course_id": s.course_id, "status": s.status, "step_ms": s.step_ms, "audio_mode": s.audio_mode,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "roster_count": roster, "checked_in_count": checked_in,
    }


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    course = db.get(Course, body.course_id)
    if not course or course.professor_id != prof.id:
        raise HTTPException(404, "COURSE_NOT_FOUND")
    open_one = db.scalar(select(ClassSession.id).where(ClassSession.course_id == course.id, ClassSession.status.in_(("created", "live", "paused"))))
    if open_one:  # one open session per course: close the previous one first
        raise HTTPException(409, "OPEN_SESSION_EXISTS")
    s = ClassSession(course_id=course.id, step_ms=body.step_ms, audio_mode=body.audio_mode, secret=secrets.token_bytes(32))
    db.add(s)
    db.flush()
    audit(db, prof.id, "session.created", s.id)
    db.commit()
    return _out(db, s)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _out(db, _own_session(db, session_id, prof))


def _transition(db: Session, session_id: str, prof: User, allowed_from: str, to: str):
    s = _own_session(db, session_id, prof)
    if s.status != allowed_from:
        raise HTTPException(409, "INVALID_STATE")
    s.status = to
    if to == "live" and allowed_from == "created":
        s.started_at = datetime.now(timezone.utc)
    audit(db, prof.id, f"session.{to}", s.id)
    db.commit()
    hub.publish(s.id, "session.state", {"status": to})
    return _out(db, s)


@router.post("/sessions/{session_id}/start")
def start(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _transition(db, session_id, prof, "created", "live")


@router.post("/sessions/{session_id}/pause")
def pause(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _transition(db, session_id, prof, "live", "paused")


@router.post("/sessions/{session_id}/resume")
def resume(session_id: str, prof: User = Depends(prof_only), db: Session = Depends(get_db)):
    return _transition(db, session_id, prof, "paused", "live")


@router.get("/sessions/{session_id}/tokens")
def get_tokens(
    session_id: str,
    from_window: int | None = Query(default=None, alias="from"),
    count: int = Query(default=config.TOKEN_MAX_BATCH, ge=1, le=config.TOKEN_MAX_BATCH),
    prof: User = Depends(prof_only),
    db: Session = Depends(get_db),
):
    s = _own_session(db, session_id, prof)
    if s.status != "live":
        raise HTTPException(409, "SESSION_NOT_LIVE")
    now_ms = int(time.time() * 1000)
    now_w = tokens.window_for(now_ms, s.step_ms)
    first = now_w if from_window is None else from_window
    last_allowed = tokens.window_for(now_ms + config.TOKEN_AHEAD_MS, s.step_ms)
    if first < now_w - config.GRACE_WINDOWS:
        raise HTTPException(422, "WINDOW_TOO_OLD")
    if first > last_allowed:
        raise HTTPException(422, "TOO_FAR_AHEAD")
    last = min(first + count - 1, last_allowed)
    return {
        "step_ms": s.step_ms, "server_ms": now_ms,
        "tokens": [
            {"window": w, "payload": tokens.make_qr_payload(s.secret, s.id, w), **({"audio_code": tokens.audio_code(s.secret, s.id, w)} if s.audio_mode == "burst" else {})}
            for w in range(first, last + 1)
        ],
    }
