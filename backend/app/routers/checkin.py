"""Student check-in: QR hard gate, then face + liveness + trust scoring (phase 5). Audio arrives in phase 6."""
import json
import math
import secrets
import time

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config, nonce, ratelimit, tokens, trust
from app.db import get_db
from app.deps import current_user, require_role
from app.events import hub
from app.models import AudioLog, Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User

router = APIRouter(tags=["student-checkin"])
student_only = require_role("student")
COLOURS = list(trust.RGB)  # saturated colours only: white has no chroma to measure


def _course_session(db: Session, session_id: str, student: User) -> ClassSession:
    """Session the student is rostered into, else 404 (never reveals sessions of other courses)."""
    s = db.get(ClassSession, session_id)
    on_roster = s and db.scalar(select(RosterEntry.id).where(RosterEntry.course_id == s.course_id, RosterEntry.student_id == student.id))
    if not s or not on_roster:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    return s


def _require_live_and_enrolled(db: Session, s: ClassSession, student: User) -> None:
    if s.status != "live":
        raise HTTPException(404, "SESSION_NOT_ACTIVE")
    tpl = db.get(FaceTemplate, student.id)
    if not tpl or tpl.status != "confirmed":
        raise HTTPException(409, "NOT_ENROLLED")
    if config.FACE_MODE == "required" and tpl.model != config.FACE_MODEL:
        raise HTTPException(409, "REENROLL_REQUIRED")  # template made by a placeholder or older model


@router.get("/sessions/active")
def active_sessions(student: User = Depends(student_only), db: Session = Depends(get_db)):
    q = (
        select(ClassSession, Course.name)
        .join(Course, Course.id == ClassSession.course_id)
        .join(RosterEntry, RosterEntry.course_id == Course.id)
        .where(RosterEntry.student_id == student.id, ClassSession.status.in_(("live", "paused")))
        .order_by(ClassSession.started_at.desc())
    )
    done = set(db.scalars(select(Checkin.session_id).where(Checkin.student_id == student.id)))
    return [{"id": s.id, "course_id": s.course_id, "course_name": name, "status": s.status, "step_ms": s.step_ms, "checked_in": s.id in done} for s, name in db.execute(q)]


@router.post("/sessions/{session_id}/challenge")
def issue_challenge(session_id: str, student: User = Depends(student_only), db: Session = Depends(get_db)):
    """Random colour sequence for the colour-flash liveness step; consumed once by /checkin."""
    ratelimit.hit("challenge", student.id, 20, 60)
    s = _course_session(db, session_id, student)
    _require_live_and_enrolled(db, s, student)
    expires = int(time.time() * 1000) + config.CHALLENGE_TTL_MS
    c = Challenge(session_id=s.id, student_id=student.id, colour_seq=[secrets.choice(COLOURS) for _ in range(4)], expires_ms=expires)
    db.add(c)
    db.commit()
    return {"challenge_id": c.id, "expires_ms": expires, "colour_seq": c.colour_seq}


class FaceIn(BaseModel):
    """Numbers only: the 128-d descriptor computed on the phone. No image ever leaves the device at check-in."""
    vector: list[float] = Field(min_length=128, max_length=128)
    model: str = Field(max_length=32)

    @field_validator("vector")
    @classmethod
    def _finite(cls, v):
        if not all(math.isfinite(x) for x in v):
            raise ValueError("non-finite")
        return v


class LivenessIn(BaseModel):
    baseline: list[float] = Field(min_length=3, max_length=3)       # mean face RGB before the flashes
    samples: list[list[float]] = Field(min_length=1, max_length=8)  # mean face RGB during each colour

    @field_validator("baseline", "samples")
    @classmethod
    def _rgb(cls, v):
        rows = v if isinstance(v[0], list) else [v]
        if any(len(r) != 3 or not all(math.isfinite(x) and 0 <= x <= 255 for x in r) for r in rows):
            raise ValueError("bad rgb")
        return v


class AudioIn(BaseModel):
    """What the phone's microphone made of the ultrasonic burst: three numbers, never sound."""
    status: Literal["heard", "none_heard", "denied", "unsupported"]
    window_low8: int | None = Field(default=None, ge=0, le=255)
    code: int | None = Field(default=None, ge=0, le=65535)
    snr_db: float | None = Field(default=None, ge=-50, le=100)


class CheckinIn(BaseModel):
    qr: str = Field(min_length=1, max_length=256)
    challenge_id: str | None = Field(default=None, max_length=64)
    face: FaceIn | None = None
    liveness: LivenessIn | None = None
    audio: AudioIn | None = None
    scanned_at_client_ms: int | None = None
    device: dict | None = None


def _result(c: Checkin) -> dict:
    return {"checkin_id": c.id, "status": c.status, "score": c.score, "flags": c.flags, "latency_ms": c.latency_ms}


def _assess(db: Session, s: ClassSession, student: User, body: CheckinIn):
    """Soft signals -> (status, score, flags, evidence, stored_vector). Nothing here can reject; the QR gate already passed."""
    if config.FACE_MODE == "off":
        return "VERIFIED", 0.5, ["QR_ONLY"], None, None
    evidence: dict = {}
    audio = _audio_verdict(s, body.audio, evidence) if s.audio_mode == "burst" else None
    face_dist, vec, collision = None, None, False
    tpl = db.get(FaceTemplate, student.id)
    if body.face and body.face.model == config.FACE_MODEL and tpl and tpl.model == config.FACE_MODEL:
        vec = body.face.vector
        face_dist = trust.distance(vec, tpl.vector)
        evidence["face_dist"] = round(face_dist, 4)
        if face_dist > config.FACE_DIST_MAX:  # who does this face look like? helps the professor, never auto-decides
            others = db.execute(select(FaceTemplate.student_id, FaceTemplate.vector).join(RosterEntry, RosterEntry.student_id == FaceTemplate.student_id)
                                .where(RosterEntry.course_id == s.course_id, FaceTemplate.status == "confirmed", FaceTemplate.student_id != student.id)).all()
            best = min(((trust.distance(vec, v), sid) for sid, v in others), default=None)
            if best and best[0] <= config.FACE_DIST_MAX:
                evidence["looks_like"] = {"student_id": best[1], "dist": round(best[0], 4)}
        near = [sid for sid, v in db.execute(select(Checkin.student_id, Checkin.face_vector).where(Checkin.session_id == s.id, Checkin.face_vector.is_not(None))).all()
                if trust.distance(vec, v) <= config.COLLISION_DIST]
        if near:
            collision = True
            evidence["collided_with"] = near
    live_ok = None
    if body.liveness and body.challenge_id:
        ch = db.get(Challenge, body.challenge_id)
        valid = ch and ch.session_id == s.id and ch.student_id == student.id and not ch.used and ch.expires_ms >= int(time.time() * 1000)
        if valid:
            ch.used = True
            live_ok, cos = trust.liveness(ch.colour_seq, body.liveness.baseline, body.liveness.samples)
            evidence["liveness_cos"] = [round(x, 3) for x in cos]
        else:
            live_ok = False
            evidence["challenge"] = "invalid_or_expired"
    status, total, flags = trust.score(face_dist=face_dist, live_ok=live_ok, collision=collision, audio=audio)
    return status, total, flags, evidence, vec


def _audio_verdict(s: ClassSession, a: AudioIn | None, evidence: dict) -> str:
    """ok if the heard code matches a window within AUDIO_MAX_AGE_MS; mismatch if a CRC-valid burst is wrong or too old (replay); else unavailable."""
    if not a or a.status != "heard" or a.window_low8 is None or a.code is None:
        evidence["audio"] = a.status if a else "absent"
        return "unavailable"
    now_w = tokens.window_for(int(time.time() * 1000), s.step_ms)
    span = -(-config.AUDIO_MAX_AGE_MS // s.step_ms) + config.GRACE_WINDOWS
    for w in range(now_w, max(-1, now_w - span - 1), -1):
        if w % 256 == a.window_low8 and tokens.audio_code(s.secret, s.id, w) == a.code:
            evidence["audio"] = {"ok": True, "age_windows": now_w - w, "snr_db": a.snr_db}
            return "ok"
    evidence["audio"] = {"ok": False, "low8": a.window_low8, "snr_db": a.snr_db}
    return "mismatch"


def _publish(db: Session, s: ClassSession, c: Checkin, type_: str) -> None:
    """Live feed for the professor console. Names are fine here: this stream is professor-only."""
    u = db.get(User, c.student_id)
    hub.publish(s.id, type_, {"student_id": c.student_id, "name": u.name, "roll_no": u.roll_no, "status": c.status, "score": c.score, "flags": c.flags})
    if c.status == "REVIEW":
        hub.publish(s.id, "attention.raised", {"student_id": c.student_id, "name": u.name, "roll_no": u.roll_no, "kind": "flagged", "flags": c.flags,
                                               "has_photo": db.scalar(select(FaceTemplate.student_id).where(FaceTemplate.student_id == c.student_id, FaceTemplate.photo_enc.is_not(None))) is not None})


def _mark_collisions(db: Session, s: ClassSession, mine: Checkin, others: list[str]) -> None:
    """The other student(s) with the same face in this session also go to REVIEW, pointing back at this one."""
    for o in db.scalars(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id.in_(others))):
        if "FACE_COLLISION" not in o.flags:
            o.flags = [*o.flags, "FACE_COLLISION"]
        o.status = "REVIEW"
        o.evidence = {**(o.evidence or {}), "collided_with": sorted({*(o.evidence or {}).get("collided_with", []), mine.student_id})}
    db.commit()
    for o in db.scalars(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id.in_(others))):
        _publish(db, s, o, "checkin.updated")


@router.post("/sessions/{session_id}/checkin")
def checkin(session_id: str, body: CheckinIn, student: User = Depends(student_only), db: Session = Depends(get_db)):
    t0 = time.perf_counter()
    ratelimit.hit("checkin", student.id, 60, 60)
    s = _course_session(db, session_id, student)
    existing = db.scalar(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id == student.id))
    if existing:  # a retry after a lost response returns the same result
        return _result(existing)
    _require_live_and_enrolled(db, s, student)

    parsed = tokens.parse_qr(body.qr)
    if not parsed or parsed.session_id != s.id:
        raise HTTPException(403, "TOKEN_INVALID")
    verdict = tokens.verify(s.secret, parsed, int(time.time() * 1000), s.step_ms, config.GRACE_WINDOWS)
    if verdict == "invalid":
        raise HTTPException(403, "TOKEN_INVALID")
    if verdict == "expired":
        raise HTTPException(410, "TOKEN_EXPIRED")
    if not nonce.claim(s.id, parsed.window, student.id):
        raise HTTPException(409, "IN_PROGRESS")

    status, total, flags, evidence, vec = _assess(db, s, student, body)
    c = Checkin(
        session_id=s.id, student_id=student.id, status=status, score=total, flags=flags, face_vector=vec, evidence=evidence, audio=body.audio.model_dump() if body.audio else None,
        device={**(body.device or {}), "scanned_at_client_ms": body.scanned_at_client_ms, "challenge_id": body.challenge_id},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    db.add(c)
    try:
        db.commit()
    except IntegrityError:  # two simultaneous submissions: keep the first
        db.rollback()
        return _result(db.scalar(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id == student.id)))
    _publish(db, s, c, "checkin.created")
    if "FACE_COLLISION" in flags:
        _mark_collisions(db, s, c, evidence["collided_with"])
    return _result(c)


@router.get("/sessions/{session_id}/checkin/me")
def my_checkin(session_id: str, student: User = Depends(student_only), db: Session = Depends(get_db)):
    s = _course_session(db, session_id, student)
    c = db.scalar(select(Checkin).where(Checkin.session_id == s.id, Checkin.student_id == student.id))
    return _result(c) if c else {"status": "PENDING"}


@router.get("/sessions/{session_id}/audio-plan")
def audio_plan(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Burst parameters for the emitter (professor) and the listener (student). Same numbers as shared/audiocodec.ts."""
    if user.role == "professor":
        s = db.get(ClassSession, session_id)
        course = db.get(Course, s.course_id) if s else None
        if not s or course.professor_id != user.id:
            raise HTTPException(404, "SESSION_NOT_FOUND")
    else:
        s = _course_session(db, session_id, user)
    return {
        "mode": s.audio_mode, "step_ms": s.step_ms, "band_hz": list(config.AUDIO_BAND_HZ), "sample_rate": config.AUDIO_SAMPLE_RATE,
        "max_age_ms": config.AUDIO_MAX_AGE_MS, "trial_mode": config.TRIAL_MODE,
    }


class AudioLogIn(BaseModel):
    session_id: str = Field(max_length=64)
    data: dict


@router.post("/trial/audio-log", status_code=204)
def audio_log(body: AudioLogIn, student: User = Depends(student_only), db: Session = Depends(get_db)):
    """Field-trial telemetry. Silently ignored outside trial mode so production never stores it."""
    if not config.TRIAL_MODE:
        return
    s = _course_session(db, body.session_id, student)
    if len(json.dumps(body.data)) > 4000:
        raise HTTPException(413, "LOG_TOO_LARGE")
    db.add(AudioLog(session_id=s.id, student_id=student.id, payload=body.data))
    db.commit()
