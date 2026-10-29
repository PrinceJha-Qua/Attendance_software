"""Phase 9 checks written by the builder: error envelope, headers, rate limits, retention, prod guards."""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import retention
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User
from app.security import hash_password


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app, raise_server_exceptions=False)


def test_unknown_route_and_bad_method_use_the_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND" and r.json()["error"]["request_id"]
    r = client.delete("/api/v1/healthz")
    assert r.status_code == 405 and r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_security_headers_and_request_id_match(client):
    r = client.get("/api/v1/healthz")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store" and r.headers["x-request-id"]
    e = client.get("/api/v1/nope")
    assert e.json()["error"]["request_id"] == e.headers["x-request-id"]


def test_login_locks_after_ten_failures_and_success_clears(client):
    ident = f"nobody_{uuid.uuid4().hex[:6]}@example.edu"
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"identifier": ident, "password": "x"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"identifier": ident, "password": "x"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    # a real account: a few failures then success resets the counter
    for _ in range(3):
        client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "bad"})
    assert client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"}).status_code == 200
    for _ in range(9):
        client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "bad"})
    assert client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"}).status_code == 200


def test_login_is_audited(client):
    client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"})
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.login"))


def test_retention_daily_and_term_end():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        c = Course(professor_id=prof.id, name=f"P9 {tag}")
        u = User(email=f"p9_{tag}@example.edu", roll_no=f"P9{tag}", name="P9", role="student", password_hash=hash_password("x"))
        db.add_all([c, u]); db.flush()
        db.add_all([RosterEntry(course_id=c.id, student_id=u.id), FaceTemplate(student_id=u.id, vector=[0.0] * 128, model="faceapi-128-v1", status="confirmed", photo_enc=b"x")])
        s = ClassSession(course_id=c.id, status="closed", secret=b"k" * 32)
        db.add(s); db.flush()
        old = Checkin(session_id=s.id, student_id=u.id, status="VERIFIED", face_vector=[0.0] * 128, created_at=datetime.now(timezone.utc) - timedelta(days=45))
        db.add(old); db.commit()
        ids = (c.id, u.id, s.id, old.id)
    try:
        with SessionLocal() as db:
            out = retention.daily(db)
            assert out["checkin_vectors_cleared"] >= 1
            ck = db.get(Checkin, ids[3]); assert ck.face_vector is None and ck.status == "VERIFIED"  # the record stays
            assert db.get(FaceTemplate, ids[1]) is not None  # daily never touches templates
            out = retention.term_end(db, [ids[0]])
            assert out["templates_deleted"] == 1 and db.get(FaceTemplate, ids[1]) is None
            assert db.scalar(select(AuditLog).where(AuditLog.action == "retention.term_end", AuditLog.target.like(f"{ids[0]}%")))
    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.id == ids[3]).delete(); db.query(ClassSession).filter(ClassSession.id == ids[2]).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id == ids[0]).delete(); db.query(FaceTemplate).filter(FaceTemplate.student_id == ids[1]).delete()
            db.query(Course).filter(Course.id == ids[0]).delete(); db.query(User).filter(User.id == ids[1]).delete(); db.commit()


@pytest.mark.parametrize("env,expect", [
    ({"GK_ENV": "prod"}, "GK_SECRET_KEY"),
    ({"GK_ENV": "prod", "GK_SECRET_KEY": "x" * 40, "GK_PHOTO_KEY": "k", "GK_REDIS_URL": "redis://r", "GK_DATABASE_URL": "sqlite:///x.db"}, "Postgres"),
    ({"GK_ENV": "prod", "GK_SECRET_KEY": "x" * 40, "GK_PHOTO_KEY": "k", "GK_REDIS_URL": "redis://r", "GK_DATABASE_URL": "postgresql://u@h/d", "GK_FACE_MODE": "off"}, "FACE_MODE"),
])
def test_prod_refuses_unsafe_config(env, expect):
    base = {k: v for k, v in os.environ.items() if not k.startswith("GK_")}
    r = subprocess.run([sys.executable, "-c", "import app.config"], env={**base, **env}, capture_output=True, text=True)
    assert r.returncode != 0 and expect in r.stderr
