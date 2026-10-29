"""Retention: biometric data does not outlive its purpose.

daily():     session-scoped face vectors on check-ins, phone audio telemetry and spent challenges age out.
term_end():  face templates (vector + reference photo) of every student on the given courses are deleted.
Attendance records, check-in statuses and the audit log are the academic record and are kept."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import config
from app.models import AudioLog, Challenge, Checkin, FaceTemplate, RosterEntry
from app.storage import audit


def daily(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    vec_cut = now - timedelta(days=config.RETAIN_CHECKIN_VECTOR_DAYS)
    tel_cut = now - timedelta(days=config.RETAIN_TELEMETRY_DAYS)
    vec = db.execute(update(Checkin).where(Checkin.face_vector.is_not(None), Checkin.created_at < vec_cut).values(face_vector=None)).rowcount
    aud = db.execute(delete(AudioLog).where(AudioLog.at < tel_cut)).rowcount
    chal = db.execute(delete(Challenge).where(Challenge.expires_ms < int((now - timedelta(days=1)).timestamp() * 1000))).rowcount
    out = {"checkin_vectors_cleared": vec, "audio_logs_deleted": aud, "challenges_deleted": chal}
    audit(db, None, "retention.daily", str(out))
    db.commit()
    return out


def term_end(db: Session, course_ids: list[str]) -> dict:
    ids = list(db.scalars(select(RosterEntry.student_id).where(RosterEntry.course_id.in_(course_ids)).distinct()))
    n = db.execute(delete(FaceTemplate).where(FaceTemplate.student_id.in_(ids))).rowcount if ids else 0
    vec = db.execute(update(Checkin).where(Checkin.student_id.in_(ids), Checkin.face_vector.is_not(None)).values(face_vector=None)).rowcount if ids else 0
    out = {"students": len(ids), "templates_deleted": n, "checkin_vectors_cleared": vec}
    audit(db, None, "retention.term_end", f"{','.join(course_ids)}:{out}")
    db.commit()
    return out
