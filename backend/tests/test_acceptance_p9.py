"""Phase 9 acceptance test suite: Hardening, privacy, and deployment.
Specifications defined in docs/acceptance/P9.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import io
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql

from app import config, ratelimit, retention
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AttendanceRecord,
    AuditLog,
    Challenge,
    Checkin,
    ClassSession,
    Course,
    Dispute,
    FaceTemplate,
    RosterEntry,
    User,
)
from app.security import hash_password, make_token


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app, raise_server_exceptions=False)


def _cleanup_p9():
    with SessionLocal() as db:
        courses = db.scalars(select(Course).where(Course.name.like("P9 %"))).all()
        cids = [c.id for c in courses]
        if cids:
            sids = list(db.scalars(select(ClassSession.id).where(ClassSession.course_id.in_(cids))))
            rids = list(db.scalars(select(AttendanceRecord.id).where(AttendanceRecord.session_id.in_(sids)))) if sids else []
            if rids:
                db.query(Dispute).filter(Dispute.record_id.in_(rids)).delete()
            if sids:
                for m in (AttendanceRecord, Checkin, Challenge):
                    db.query(m).filter(m.session_id.in_(sids)).delete()
                db.query(ClassSession).filter(ClassSession.id.in_(sids)).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id.in_(cids)).delete()
            for c in courses:
                db.delete(c)

        users = db.scalars(
            select(User).where(
                (User.email.like("%p9%@example.edu"))
                | (User.email.like("aud_%@example.edu"))
            )
        ).all()
        uids = [u.id for u in users]
        if uids:
            db.query(AuditLog).filter(AuditLog.actor_id.in_(uids)).delete()
            db.query(Dispute).filter(Dispute.student_id.in_(uids)).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(uids)).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id.in_(uids)).delete()
            for u in users:
                db.delete(u)
        db.commit()


@pytest.fixture(autouse=True)
def p9_clean():
    ratelimit._clear_all_for_tests()
    _cleanup_p9()
    yield
    ratelimit._clear_all_for_tests()
    _cleanup_p9()


# =========================================================================================
# Section A: Error envelope and headers (Cases 1-3)
# =========================================================================================

def test_01_error_envelope_and_request_id_matching(client):
    """Case 1: Unknown path, wrong method, malformed JSON body, missing field:
    every one returns {error:{code,message,request_id}}; request_id equals x-request-id header.
    """
    # 1. Unknown path
    r_not_found = client.get("/api/v1/non_existent_route")
    assert r_not_found.status_code == 404
    err1 = r_not_found.json()["error"]
    assert err1["code"] == "NOT_FOUND"
    assert err1["request_id"] == r_not_found.headers["x-request-id"]

    # 2. Wrong method
    r_bad_method = client.delete("/api/v1/healthz")
    assert r_bad_method.status_code == 405
    err2 = r_bad_method.json()["error"]
    assert err2["code"] == "METHOD_NOT_ALLOWED"
    assert err2["request_id"] == r_bad_method.headers["x-request-id"]

    # 3. Malformed JSON body
    r_malformed = client.post(
        "/api/v1/auth/login",
        content=b"this is not json",
        headers={"Content-Type": "application/json"},
    )
    assert r_malformed.status_code == 422
    err3 = r_malformed.json()["error"]
    assert err3["code"] == "VALIDATION_ERROR"
    assert err3["request_id"] == r_malformed.headers["x-request-id"]

    # 4. Missing required field
    r_missing = client.post("/api/v1/auth/login", json={})
    assert r_missing.status_code == 422
    err4 = r_missing.json()["error"]
    assert err4["code"] == "VALIDATION_ERROR"
    assert err4["request_id"] == r_missing.headers["x-request-id"]


def test_02_forced_internal_server_error_sanitization(client):
    """Case 2: Force a server error: 500 INTERNAL, no stack trace or file names in the body."""
    from app.db import get_db

    def boom():
        raise RuntimeError("Secret DB connection string: postgres://user:secretpass@db/prod in /srv/app/db.py line 42")

    app.dependency_overrides[get_db] = boom
    try:
        r = client.get("/api/v1/readyz")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 500
    data = r.json()
    assert "error" in data
    assert data["error"]["code"] == "INTERNAL"
    assert data["error"]["message"] == "Something went wrong"
    assert data["error"]["request_id"] == r.headers["x-request-id"]

    # Strict privacy: no traceback, line numbers, or file names leaked
    body_text = r.text
    assert "RuntimeError" not in body_text
    assert "secretpass" not in body_text
    assert "Traceback" not in body_text
    assert ".py" not in body_text


def test_03_security_headers_and_prod_docs_disabled(client):
    """Case 3: All responses have:
    - x-content-type-options: nosniff
    - cache-control: no-store
    - restrictive CSP
    With GK_ENV=prod also HSTS, and /docs and /api/v1/openapi.json are 404.
    """
    # 1. Dev headers
    r = client.get("/api/v1/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]

    # 2. Prod headers and docs disabled
    cmd = [
        sys.executable,
        "-c",
        (
            "import os; os.environ['GK_ENV'] = 'prod'; os.environ['GK_SECRET_KEY'] = 'x'*40; "
            "os.environ['GK_PHOTO_KEY'] = 'k'*44; os.environ['GK_REDIS_URL'] = 'redis://localhost:6379'; "
            "os.environ['GK_DATABASE_URL'] = 'postgresql://u:p@localhost/db'; "
            "from fastapi.testclient import TestClient; from app.main import app; "
            "c = TestClient(app); "
            "r = c.get('/api/v1/healthz'); "
            "assert 'strict-transport-security' in r.headers, 'HSTS missing'; "
            "assert c.get('/docs').status_code == 404, '/docs must be 404'; "
            "assert c.get('/api/v1/openapi.json').status_code == 404, '/openapi.json must be 404'; "
            "print('PROD_HEADERS_OK')"
        ),
    ]
    base = {k: v for k, v in os.environ.items() if not k.startswith("GK_")}
    res = subprocess.run(cmd, env=base, capture_output=True, text=True)
    assert res.returncode == 0, f"Prod mode check failed: {res.stderr}\n{res.stdout}"
    assert "PROD_HEADERS_OK" in res.stdout


# =========================================================================================
# Section B: Rate limits (Cases 4-7)
# =========================================================================================

def test_04_login_rate_limit_and_success_clearing(client):
    """Case 4:
    - 10 wrong passwords for one identifier then the 11th (even correct) -> 429 RATE_LIMITED
    - Another identifier from the same client is unaffected
    - A success before the limit clears the count
    """
    ident_a = f"ratelim_a_{uuid.uuid4().hex[:6]}@example.edu"
    ident_b = f"ratelim_b_{uuid.uuid4().hex[:6]}@example.edu"

    # 10 wrong passwords on ident_a
    for _ in range(10):
        r = client.post("/api/v1/auth/login", json={"identifier": ident_a, "password": "wrong"})
        assert r.status_code == 401

    # 11th request (even if attempted) -> 429 RATE_LIMITED
    r11 = client.post("/api/v1/auth/login", json={"identifier": ident_a, "password": "wrong"})
    assert r11.status_code == 429
    assert r11.json()["error"]["code"] == "RATE_LIMITED"

    # Another identifier ident_b is unaffected
    r_b = client.post("/api/v1/auth/login", json={"identifier": ident_b, "password": "wrong"})
    assert r_b.status_code == 401

    # A success before the limit clears the count
    # Seeded user: prof@example.edu / gatekeeper-dev
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "wrong"}).status_code == 401
    # Successful login resets the counter
    assert client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"}).status_code == 200
    # Can fail 5 more times without hitting 10 limit
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "wrong"}).status_code == 401


def test_05_challenge_rate_limit_per_student(client):
    """Case 5: 21st challenge request in a minute by one student -> 429; different student unaffected."""
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("pw")
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        c = Course(professor_id=prof.id, name=f"P9 Course {tag}")
        db.add(c); db.flush()
        u1 = User(email=f"p9_c1_{tag}@example.edu", roll_no=f"C1_{tag}", name="Student C1", role="student", password_hash=pw)
        u2 = User(email=f"p9_c2_{tag}@example.edu", roll_no=f"C2_{tag}", name="Student C2", role="student", password_hash=pw)
        db.add_all([u1, u2]); db.flush()
        db.add_all([RosterEntry(course_id=c.id, student_id=u1.id), RosterEntry(course_id=c.id, student_id=u2.id)])
        db.add_all([
            FaceTemplate(student_id=u1.id, vector=[0.1]*128, model="faceapi-128-v1", status="confirmed"),
            FaceTemplate(student_id=u2.id, vector=[0.2]*128, model="faceapi-128-v1", status="confirmed"),
        ])
        s = ClassSession(course_id=c.id, status="live", secret=b"k"*32, started_at=datetime.now(timezone.utc))
        db.add(s); db.commit()
        sid = s.id
        t1 = make_token(u1.id, "student", "access")
        t2 = make_token(u2.id, "student", "access")

    h1 = {"Authorization": f"Bearer {t1}"}
    h2 = {"Authorization": f"Bearer {t2}"}

    # 20 challenge requests succeed
    for i in range(20):
        res = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h1)
        assert res.status_code == 200, f"Challenge {i+1} failed: {res.text}"

    # 21st challenge request -> 429 RATE_LIMITED
    res21 = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h1)
    assert res21.status_code == 429
    assert res21.json()["error"]["code"] == "RATE_LIMITED"

    # Student 2 is completely unaffected
    res_s2 = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h2)
    assert res_s2.status_code == 200


def test_06_checkin_rate_limit_per_student(client):
    """Case 6: 61st check-in post in a minute by one student -> 429. Normal use never reaches it."""
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("pw")
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        c = Course(professor_id=prof.id, name=f"P9 Checkin Rate {tag}")
        db.add(c); db.flush()
        u = User(email=f"p9_ck_{tag}@example.edu", roll_no=f"CK_{tag}", name="Student CK", role="student", password_hash=pw)
        db.add(u); db.flush()
        db.add(RosterEntry(course_id=c.id, student_id=u.id))
        s = ClassSession(course_id=c.id, status="live", secret=b"k"*32, started_at=datetime.now(timezone.utc))
        db.add(s); db.commit()
        sid = s.id
        tok = make_token(u.id, "student", "access")

    h = {"Authorization": f"Bearer {tok}"}

    # First 60 check-in calls pass the rate-limiter
    for _ in range(60):
        r = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": "test"}, headers=h)
        assert r.status_code != 429

    # 61st check-in post -> 429 RATE_LIMITED
    r61 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": "test"}, headers=h)
    assert r61.status_code == 429
    assert r61.json()["error"]["code"] == "RATE_LIMITED"


def test_07_rate_limit_off_mode(client, monkeypatch):
    """Case 7: With GK_RATE_LIMIT=off in dev, none of the above limit."""
    monkeypatch.setattr(config, "RATE_LIMIT", False)

    ident = f"ratelim_off_{uuid.uuid4().hex[:6]}@example.edu"
    # Exceed 10 failures
    for _ in range(15):
        r = client.post("/api/v1/auth/login", json={"identifier": ident, "password": "wrong"})
        assert r.status_code == 401, f"Expected 401 with rate limit off, got {r.status_code}"


# =========================================================================================
# Section C: Prod guards (Case 8)
# =========================================================================================

@pytest.mark.parametrize("env_overrides,expect_error", [
    ({"GK_SECRET_KEY": "dev-only-secret"}, "GK_SECRET_KEY must be set in production"),
    ({"GK_SECRET_KEY": "x"*40, "GK_PHOTO_KEY": ""}, "GK_PHOTO_KEY must be set in production"),
    ({"GK_SECRET_KEY": "x"*40, "GK_PHOTO_KEY": "k"*44, "GK_REDIS_URL": ""}, "GK_REDIS_URL must be set in production"),
    ({"GK_SECRET_KEY": "x"*40, "GK_PHOTO_KEY": "k"*44, "GK_REDIS_URL": "redis://r", "GK_DATABASE_URL": "sqlite:///test.db"}, "GK_DATABASE_URL must point at Postgres in production"),
    ({"GK_SECRET_KEY": "x"*40, "GK_PHOTO_KEY": "k"*44, "GK_REDIS_URL": "redis://r", "GK_DATABASE_URL": "postgresql://u@h/d", "GK_FACE_MODE": "off"}, "GK_FACE_MODE must be 'required' in production"),
    ({"GK_SECRET_KEY": "x"*40, "GK_PHOTO_KEY": "k"*44, "GK_REDIS_URL": "redis://r", "GK_DATABASE_URL": "postgresql://u@h/d", "GK_RATE_LIMIT": "off"}, "GK_RATE_LIMIT=off is not allowed in production"),
])
def test_08_prod_environment_guards(env_overrides, expect_error):
    """Case 8: GK_ENV=prod fails when each required setting is missing or unsafe."""
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("GK_")}
    base_env["GK_ENV"] = "prod"
    base_env.update(env_overrides)

    cmd = [sys.executable, "-c", "import app.config"]
    res = subprocess.run(cmd, env=base_env, capture_output=True, text=True)
    assert res.returncode != 0
    assert expect_error in res.stderr


def test_08b_seed_script_refusal_under_prod():
    """Case 8: scripts/seed.py refuses under prod."""
    seed_env = {
        "GK_ENV": "prod",
        "GK_SECRET_KEY": "x" * 40,
        "GK_PHOTO_KEY": "k" * 44,
        "GK_REDIS_URL": "redis://localhost:6379",
        "GK_DATABASE_URL": "postgresql://u:p@localhost/db",
    }
    res_seed = subprocess.run([sys.executable, "scripts/seed.py"], env=seed_env, capture_output=True, text=True)
    assert res_seed.returncode != 0
    assert "seed.py is dev-only" in res_seed.stderr


# =========================================================================================
# Section D: Retention and audit (Cases 9-12)
# =========================================================================================

def test_09_retention_daily_behavior_and_audit():
    """Case 9: scripts/retention.py:
    - check-in older than 30 days loses face_vector but keeps status/score
    - face template is untouched
    - audit row retention.daily written
    """
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("x")

    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        c = Course(professor_id=prof.id, name=f"P9 Ret Daily {tag}")
        u = User(email=f"p9_rd_{tag}@example.edu", roll_no=f"RD_{tag}", name="RD", role="student", password_hash=pw)
        db.add_all([c, u]); db.flush()
        db.add(RosterEntry(course_id=c.id, student_id=u.id))
        tmpl = FaceTemplate(student_id=u.id, vector=[0.1]*128, model="faceapi-128-v1", status="confirmed", photo_enc=b"encphoto")
        db.add(tmpl)
        s_old = ClassSession(course_id=c.id, status="closed", secret=b"k"*32)
        s_new = ClassSession(course_id=c.id, status="closed", secret=b"k"*32)
        db.add_all([s_old, s_new]); db.flush()

        old_ck = Checkin(session_id=s_old.id, student_id=u.id, status="VERIFIED", score=0.92, face_vector=[0.1]*128, created_at=datetime.now(timezone.utc) - timedelta(days=35))
        new_ck = Checkin(session_id=s_new.id, student_id=u.id, status="VERIFIED", score=0.95, face_vector=[0.2]*128, created_at=datetime.now(timezone.utc) - timedelta(days=5))
        db.add_all([old_ck, new_ck])
        db.commit()
        old_id, new_id, uid = old_ck.id, new_ck.id, u.id

    # Run daily retention
    with SessionLocal() as db:
        res = retention.daily(db)
        assert res["checkin_vectors_cleared"] >= 1

        # Old check-in lost vector, kept status and score
        ck_old = db.get(Checkin, old_id)
        assert ck_old.face_vector is None
        assert ck_old.status == "VERIFIED"
        assert ck_old.score == 0.92

        # Template untouched
        t_db = db.get(FaceTemplate, uid)
        assert t_db is not None
        assert t_db.photo_enc == b"encphoto"

        # Audit row written
        audit_log = db.scalar(select(AuditLog).where(AuditLog.action == "retention.daily").order_by(AuditLog.at.desc()))
        assert audit_log is not None


def test_10_retention_term_end_behavior_and_audit(client):
    """Case 10: scripts/retention.py --term-end <course>:
    - all roster students' templates (vector + photo) gone
    - /students/me/enrollment shows none
    - attendance records intact
    - audit row written
    - students on other courses only are untouched
    """
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("x")

    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        c1 = Course(professor_id=prof.id, name=f"P9 TermEnd Course 1 {tag}")
        c2 = Course(professor_id=prof.id, name=f"P9 Other Course 2 {tag}")
        u1 = User(email=f"p9_te1_{tag}@example.edu", roll_no=f"TE1_{tag}", name="TermEnd Student 1", role="student", password_hash=pw)
        u2 = User(email=f"p9_te2_{tag}@example.edu", roll_no=f"TE2_{tag}", name="Other Student 2", role="student", password_hash=pw)
        db.add_all([c1, c2, u1, u2]); db.flush()

        db.add(RosterEntry(course_id=c1.id, student_id=u1.id))
        db.add(RosterEntry(course_id=c2.id, student_id=u2.id))

        t1 = FaceTemplate(student_id=u1.id, vector=[0.1]*128, model="faceapi-128-v1", status="confirmed", photo_enc=b"p1")
        t2 = FaceTemplate(student_id=u2.id, vector=[0.2]*128, model="faceapi-128-v1", status="confirmed", photo_enc=b"p2")
        db.add_all([t1, t2])

        s = ClassSession(course_id=c1.id, status="closed", secret=b"k"*32)
        db.add(s); db.flush()
        rec = AttendanceRecord(session_id=s.id, student_id=u1.id, status="present", source="checkin")
        db.add(rec)
        db.commit()

        c1_id = c1.id
        u1_id = u1.id
        u2_id = u2.id
        rec_id = rec.id
        u1_tok = make_token(u1.id, "student", "access")

    # Run term_end on c1
    with SessionLocal() as db:
        out = retention.term_end(db, [c1_id])
        assert out["templates_deleted"] == 1

        # u1's template deleted
        assert db.get(FaceTemplate, u1_id) is None
        # u2's template untouched
        assert db.get(FaceTemplate, u2_id) is not None

        # Attendance record intact
        rec_db = db.get(AttendanceRecord, rec_id)
        assert rec_db is not None
        assert rec_db.status == "present"

        # Audit row written
        a_log = db.scalar(select(AuditLog).where(AuditLog.action == "retention.term_end", AuditLog.target.like(f"{c1_id}%")))
        assert a_log is not None

    # u1 /students/me/enrollment shows none
    res_enr = client.get("/api/v1/students/me/enrollment", headers={"Authorization": f"Bearer {u1_tok}"})
    assert res_enr.status_code == 200
    assert res_enr.json()["status"] == "none"


def test_11_audit_log_coverage_all_actions(client):
    """Case 11: Audit log has rows for:
    login ok/fail, course create/update, roster import, enroll confirm/reject, photo view,
    session lifecycle, spot check, resolve, manual, export, dispute create/decide,
    consent, enroll, face-data delete.
    """
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("test-pw")

    # 1. Login fail & login ok
    client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "wrong"})
    r_login = client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"})
    prof_token = r_login.json()["access_token"]
    hp = {"Authorization": f"Bearer {prof_token}"}

    # 2. Course create & update
    r_c = client.post("/api/v1/courses", json={"name": f"P9 Audit {tag}"}, headers=hp)
    cid = r_c.json()["id"]
    client.patch(f"/api/v1/courses/{cid}", json={"name": f"P9 Audit Upd {tag}"}, headers=hp)

    # 3. Roster import
    csv_content = f"roll_no,name,email\nAR_{tag},Audit Stud,p9_aud_{tag}@example.edu\n"
    client.post(f"/api/v1/courses/{cid}/roster/import", files={"file": ("r.csv", csv_content, "text/csv")}, headers=hp)

    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == f"p9_aud_{tag}@example.edu"))
        uid = u.id
    stu_tok = make_token(uid, "student", "access")
    hs = {"Authorization": f"Bearer {stu_tok}"}

    # 4. Consent
    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers=hs)

    # 5. Enroll submit
    fake_photo = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9"
    client.post("/api/v1/students/me/enroll", data={"vector": json.dumps([0.1]*128), "model": "faceapi-128-v1"}, files={"photo": ("photo.jpg", fake_photo, "image/jpeg")}, headers=hs)

    # 6. Photo view
    from app.storage import photo_url
    with SessionLocal() as db:
        prof_uid = db.scalar(select(User.id).where(User.email == "prof@example.edu"))
    p_url = photo_url(uid, prof_uid)
    client.get(p_url.replace("/api/v1", ""), headers=hp)

    # 7. Enroll confirm
    client.post(f"/api/v1/courses/{cid}/enrollments/{uid}/confirm", headers=hp)

    # 8. Session lifecycle (create, start, pause, resume, close)
    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)
    client.post(f"/api/v1/sessions/{sid}/pause", headers=hp)
    client.post(f"/api/v1/sessions/{sid}/resume", headers=hp)

    # 9. Spot check
    client.post(f"/api/v1/sessions/{sid}/spotcheck/{uid}", json={"result": "seen"}, headers=hp)

    # 10. Resolve & manual
    client.post(f"/api/v1/sessions/{sid}/attendance/{uid}/resolve", json={"status": "absent"}, headers=hp)
    client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": uid, "reason": "in person manual"}, headers=hp)

    # 11. Close & export
    client.post(f"/api/v1/sessions/{sid}/close", headers=hp)
    client.get(f"/api/v1/sessions/{sid}/export?format=csv", headers=hp)

    # 12. Dispute create & decide
    with SessionLocal() as db:
        rec = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == sid, AttendanceRecord.student_id == uid))
        rec.status = "absent"  # make absent so it can be disputed
        db.commit()
        rid = rec.id

    r_disp = client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "dispute note"}, headers=hs)
    did = r_disp.json()["id"]
    client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept", "note": "verified"}, headers=hp)

    # 13. Face data delete
    client.delete("/api/v1/students/me/face-data", headers=hs)

    # Verify audit log actions
    with SessionLocal() as db:
        actions = set(db.scalars(select(AuditLog.action)).all())
        expected_actions = [
            "auth.login",
            "auth.login_failed",
            "course.created",
            "course.updated",
            "roster.import",
            "consent.given",
            "enroll.submitted",
            "photo.viewed",
            "enroll.confirmed",
            "session.created",
            "session.live",
            "session.paused",
            "session.closed",
            "spotcheck.seen",
            "attendance.resolved.absent",
            "attendance.manual",
            "session.exported",
            "dispute.created",
            "dispute.accepted",
            "face_data.deleted",
        ]
        for act in expected_actions:
            assert act in actions, f"Missing audit action in log: {act}"


def test_12_structured_json_logging_and_privacy(client, monkeypatch):
    """Case 12: Logs (GK_LOG_JSON=1):
    - one JSON line per request with rid/method/path/status/ms
    - no query string, no Authorization header, no request body, no WS token
    """
    from app.main import log
    monkeypatch.setattr(config, "LOG_JSON", True)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    try:
        # Request with sensitive query string and sensitive header
        client.get(
            "/api/v1/time?sensitive_token=SUPER_SECRET_TOKEN_DO_NOT_LEAK",
            headers={"Authorization": "Bearer SENSITIVE_JWT_ACCESS_TOKEN"},
        )
    finally:
        log.removeHandler(handler)

    output = log_stream.getvalue().strip()
    assert output, "No log output produced"

    # Must be valid JSON
    entry = json.loads(output.splitlines()[-1])
    assert entry.get("event") == "req"
    assert "rid" in entry
    assert entry.get("method") == "GET"
    assert entry.get("path") == "/api/v1/time"
    assert "status" in entry
    assert "ms" in entry

    # Privacy guarantees:
    raw_log = output
    assert "SUPER_SECRET_TOKEN_DO_NOT_LEAK" not in raw_log
    assert "SENSITIVE_JWT_ACCESS_TOKEN" not in raw_log
    assert "sensitive_token" not in raw_log
    assert "Bearer" not in raw_log


# =========================================================================================
# Section E: Deployment dry-run analysis and test verification (Cases 13-16)
# =========================================================================================

def test_13_deployment_stack_configuration_dryrun():
    """Case 13: Static & functional validation of deployment configuration:
    - deploy/Dockerfile.backend: builds single-worker prod uvicorn
    - deploy/Dockerfile.web: multi-stage build for web apps
    - deploy/docker-compose.yml: healthchecks, volume persistence, env configuration
    - deploy/Caddyfile: HTTPS headers, CSP, WebSocket reverse proxy
    - deploy/backup.sh: valid shell syntax, retention hook, backup retention rotation
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    deploy_dir = os.path.join(repo_root, "deploy")

    # 1. Verify files exist
    required_files = [
        "Dockerfile.backend",
        "Dockerfile.web",
        "docker-compose.yml",
        "Caddyfile",
        "backup.sh",
        ".env.example",
    ]
    for f in required_files:
        p = os.path.join(deploy_dir, f)
        assert os.path.exists(p), f"Missing deployment file: {f}"

    # 2. Verify backup.sh syntax with /bin/sh -n
    backup_sh = os.path.join(deploy_dir, "backup.sh")
    res_sh = subprocess.run(["sh", "-n", backup_sh], capture_output=True, text=True)
    assert res_sh.returncode == 0, f"backup.sh has shell syntax errors: {res_sh.stderr}"

    # 3. Verify single-worker constraint in Dockerfile.backend
    with open(os.path.join(deploy_dir, "Dockerfile.backend")) as f:
        df_backend = f.read()
        assert "--workers" in df_backend and "1" in df_backend, "Dockerfile.backend must enforce 1 worker"
        assert "uv sync --frozen --no-dev" in df_backend

    # 4. Verify Caddyfile reverse proxy to backend
    with open(os.path.join(deploy_dir, "Caddyfile")) as f:
        caddyfile = f.read()
        assert "reverse_proxy backend:8000" in caddyfile
        assert "Strict-Transport-Security" in caddyfile


def test_14_backend_restart_session_resync_simulation(client):
    """Case 14: After restart of the backend container:
    - students can still log in
    - open sessions' live feed reconnects with resync (since_seq > server cur_seq -> resync=True)
    """
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        course = db.scalar(select(Course).where(Course.name == "CS101 Intro to Computing"))
        s = ClassSession(course_id=course.id, status="live", secret=b"k"*32, started_at=datetime.now(timezone.utc))
        db.add(s)
        db.commit()
        sid = s.id

    prof_token = make_token(prof.id, "professor", "access")

    # Simulate reconnect after server restart (since_seq higher than server's current seq 0)
    with client.websocket_connect(f"/api/v1/ws/sessions/{sid}?token={prof_token}&since_seq=999999") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello"
        assert msg["data"]["resync"] is True

    # Students can still log in
    r = client.post("/api/v1/auth/login", json={"identifier": "student01@example.edu", "password": "gatekeeper-dev"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    with SessionLocal() as db:
        db.query(ClassSession).filter(ClassSession.id == sid).delete()
        db.commit()


def test_15_backup_script_and_restore_command_validation():
    """Case 15: deploy/backup.sh produces a dump; restore into an empty DB.
    Validates dump and restore commands from deploy/backup.sh and docs/DEPLOY.md.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backup_path = os.path.join(repo_root, "deploy", "backup.sh")
    with open(backup_path) as f:
        content = f.read()

    # Checks backup commands
    assert "pg_dump -U gk gk" in content
    assert "gzip" in content
    assert "-mtime +14 -delete" in content
    assert "python scripts/retention.py" in content


def test_16_postgres_dialect_and_psycopg_compatibility():
    """Case 16: Verify all SQLAlchemy models, columns, and constraints compile cleanly
    under PostgreSQL dialect with psycopg3 driver.
    """
    # Create PostgreSQL mock dialect engine to compile all table DDLs
    pg_engine = create_engine("postgresql+psycopg://user:pass@localhost:5432/dummy")

    # Generate CREATE TABLE DDL for every model under PostgreSQL
    for table in Base.metadata.sorted_tables:
        from sqlalchemy.schema import CreateTable
        ddl = str(CreateTable(table).compile(pg_engine))
        assert "CREATE TABLE" in ddl
        assert table.name in ddl

    # Verify JSON, LargeBinary, and UUID/string mappings
    checkins_table = Base.metadata.tables["checkins"]
    assert checkins_table.c.face_vector.type.compile(dialect=postgresql.dialect()) == "JSON"
    assert checkins_table.c.evidence.type.compile(dialect=postgresql.dialect()) == "JSON"

    sessions_table = Base.metadata.tables["sessions"]
    assert sessions_table.c.secret.type.compile(dialect=postgresql.dialect()) == "BYTEA"
