import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    roll_no: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # student | professor
    password_hash: Mapped[str] = mapped_column(String(255))


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    professor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    planned_total_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_pct: Mapped[float] = mapped_column(Float, default=75.0)
    excused_counts: Mapped[bool] = mapped_column(Boolean, default=True)


class RosterEntry(Base):
    __tablename__ = "roster"
    __table_args__ = (UniqueConstraint("course_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    text_version: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FaceTemplate(Base):
    """One reference per student: 128-float vector + encrypted reference photo."""
    __tablename__ = "face_templates"
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    vector: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(32))
    photo_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|rejected
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClassSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="created")  # created|live|paused|closed
    step_ms: Mapped[int] = mapped_column(Integer, default=1500)
    audio_mode: Mapped[str] = mapped_column(String(16), default="off")  # off|burst
    secret: Mapped[bytes] = mapped_column(LargeBinary)  # per-session HMAC key, server-only
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Challenge(Base):
    __tablename__ = "challenges"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    colour_seq: Mapped[list] = mapped_column(JSON)
    expires_ms: Mapped[int] = mapped_column(BigInteger)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Checkin(Base):
    __tablename__ = "checkins"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # VERIFIED|REVIEW|REJECTED
    score: Mapped[float] = mapped_column(Float, default=0.0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    face_vector: Mapped[list | None] = mapped_column(JSON, nullable=True)  # session-scoped, purged by retention job
    audio: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # face distance, liveness cosines, collisions, look-alike; numbers only
    device: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AttendanceRecord(Base):
    """Final per-student outcome written when a session closes."""
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # present|absent|excused
    source: Mapped[str] = mapped_column(String(16))  # checkin|resolved|manual|default
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Spotcheck(Base):
    __tablename__ = "spotchecks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(16))  # seen|not_seen|unsure
    was_audit_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("attendance_records.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|accepted|rejected
    decided_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AudioLog(Base):
    """Field-trial telemetry from student phones (only written when GK_TRIAL_MODE=1): numbers about what the microphone heard."""
    __tablename__ = "audio_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
