"""Attention Panel: who the professor should look at in person. Flags are 'worth a 5-second look', never a verdict."""
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import AttendanceRecord, Checkin, ClassSession, FaceTemplate, RosterEntry, Spotcheck, User
from app.storage import photo_url

REASONS = {
    "FACE_MISMATCH": "Face does not match the enrolled photo",
    "FACE_COLLISION": "Same face as another check-in in this session",
    "LIVENESS_FAIL": "Live check did not pass",
    "FACE_MISSING": "Face check was skipped or could not run",
    "LIVENESS_MISSING": "Live check was skipped",
    "AUDIO_MISMATCH": "Classroom sound check did not match",
}


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d and d.tzinfo is None else d


def flagged_reasons(c: Checkin, names: dict[str, str]) -> list[str]:
    out = [REASONS[f] for f in c.flags if f in REASONS and not (f == "LIVENESS_MISSING" and "FACE_MISSING" in c.flags)]
    ev = c.evidence or {}
    out += [f"Same face as {names.get(x, 'another student')}" for x in ev.get("collided_with", [])]
    if "FACE_COLLISION" in c.flags and ev.get("collided_with"):
        out = [r for r in out if r != REASONS["FACE_COLLISION"]]
    if ev.get("looks_like"):
        out.append(f"Face resembles {names.get(ev['looks_like']['student_id'], 'another student')}")
    return out


def items(db: Session, s: ClassSession, prof: User, k: int = 5, audit_n: int = 0) -> tuple[list[dict], int]:
    """Top-k open attention items (flagged first, then overdue) plus an optional random audit sample of VERIFIED check-ins."""
    roster = {u.id: u for u in db.scalars(select(User).join(RosterEntry, RosterEntry.student_id == User.id).where(RosterEntry.course_id == s.course_id))}
    checkins = {c.student_id: c for c in db.scalars(select(Checkin).where(Checkin.session_id == s.id))}
    records = set(db.scalars(select(AttendanceRecord.student_id).where(AttendanceRecord.session_id == s.id)))
    seen = set(db.scalars(select(Spotcheck.student_id).where(Spotcheck.session_id == s.id)))
    has_photo = set(db.scalars(select(FaceTemplate.student_id).where(FaceTemplate.photo_enc.is_not(None))))
    names = {i: u.name for i, u in roster.items()}
    started = _aware(s.started_at)

    def card(sid: str, kind: str, reasons: list[str], risk: float, c: Checkin | None) -> dict:
        u = roster[sid]
        return {"student_id": sid, "name": u.name, "roll_no": u.roll_no, "kind": kind, "reasons": reasons, "risk": round(risk, 2),
                "flags": c.flags if c else [], "score": c.score if c else None, "photo_url": photo_url(sid, prof.id) if sid in has_photo else None}

    open_ids = [i for i in roster if i not in records and i not in seen]
    out = []
    for i in open_ids:
        c = checkins.get(i)
        if c and c.status == "REVIEW":
            rs = flagged_reasons(c, names) or ["Needs a quick look"]
            out.append(card(i, "flagged", rs, min(1.0, 0.6 + 0.1 * len(rs) + (0.2 if "FACE_COLLISION" in c.flags else 0)), c))
    if s.status in ("live", "paused") and started and (datetime.now(timezone.utc) - started).total_seconds() >= config.ATTENTION_GRACE_S:
        past = {}
        for sid, st in db.execute(select(AttendanceRecord.student_id, AttendanceRecord.status).join(ClassSession, ClassSession.id == AttendanceRecord.session_id)
                                  .where(ClassSession.course_id == s.course_id, ClassSession.id != s.id)):
            a, n = past.get(sid, (0, 0)); past[sid] = (a + (st == "absent"), n + 1)
        for i in open_ids:
            if i not in checkins:
                a, n = past.get(i, (0, 0))
                out.append(card(i, "overdue", ["Has not checked in yet"], 0.1 + 0.4 * (a / n if n else 0), None))
    out.sort(key=lambda x: (-x["risk"], x["name"]))
    total = len(out)
    out = out[:k]
    if audit_n:
        pool = [i for i in open_ids if (c := checkins.get(i)) and c.status == "VERIFIED"]
        out += [card(i, "audit", ["Routine random check"], 0.0, checkins[i]) for i in random.sample(pool, min(audit_n, len(pool)))]
    return out, total
