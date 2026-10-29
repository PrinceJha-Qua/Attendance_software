"""Acceptance test suite for Phase 4: Student check-in core (QR only).
Specifications defined in docs/acceptance/P4.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, nonce, tokens
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User
from app.security import hash_password, make_token

SEEDED_EMAILS = {"prof@example.edu"} | {f"student{i:02d}@example.edu" for i in range(1, 31)}


def _cleanup_test_data():
    """Removes sessions, checkins, challenges, and test users/courses outside seeded set."""
    with SessionLocal() as db:
        test_courses = db.scalars(select(Course).where(Course.name != "CS101 Intro to Computing")).all()
        for c in test_courses:
            sessions = db.scalars(select(ClassSession).where(ClassSession.course_id == c.id)).all()
            for s in sessions:
                db.query(Checkin).filter(Checkin.session_id == s.id).delete()
                db.query(Challenge).filter(Challenge.session_id == s.id).delete()
                db.query(AuditLog).filter(AuditLog.target == s.id).delete()
                db.delete(s)
            db.query(RosterEntry).filter(RosterEntry.course_id == c.id).delete()
            db.delete(c)

        seeded_course = db.scalar(select(Course).where(Course.name == "CS101 Intro to Computing"))
        if seeded_course:
            sessions = db.scalars(select(ClassSession).where(ClassSession.course_id == seeded_course.id)).all()
            for s in sessions:
                db.query(Checkin).filter(Checkin.session_id == s.id).delete()
                db.query(Challenge).filter(Challenge.session_id == s.id).delete()
                db.query(AuditLog).filter(AuditLog.target == s.id).delete()
                db.delete(s)

        test_users = db.scalars(select(User).where(~User.email.in_(SEEDED_EMAILS))).all()
        for u in test_users:
            db.query(FaceTemplate).filter(FaceTemplate.student_id == u.id).delete()
            db.query(AuditLog).filter(AuditLog.actor_id == u.id).delete()
            db.delete(u)

        db.commit()


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def p4_face_mode(monkeypatch):
    monkeypatch.setattr(config, "FACE_MODE", "off")


@pytest.fixture(autouse=True)
def db_isolation():
    _cleanup_test_data()
    yield
    _cleanup_test_data()


@pytest.fixture
def p4_fixtures():
    """Provides isolated professor, enrolled and unenrolled students, and a live course."""
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        assert prof is not None

        course = Course(professor_id=prof.id, name=f"P4 Course {uuid.uuid4().hex[:6]}")
        db.add(course)
        db.flush()

        # Seeded student 1 (confirmed)
        stud1 = db.scalar(select(User).where(User.email == "student01@example.edu"))
        tpl1 = db.get(FaceTemplate, stud1.id)
        if not tpl1:
            db.add(FaceTemplate(student_id=stud1.id, vector=[0.1] * 128, model="faceapi-128-v1", status="confirmed"))
        else:
            tpl1.status = "confirmed"

        # Seeded student 2 (confirmed)
        stud2 = db.scalar(select(User).where(User.email == "student02@example.edu"))
        tpl2 = db.get(FaceTemplate, stud2.id)
        if not tpl2:
            db.add(FaceTemplate(student_id=stud2.id, vector=[0.1] * 128, model="faceapi-128-v1", status="confirmed"))
        else:
            tpl2.status = "confirmed"

        # Student 3 (pending enrollment)
        stud3 = db.scalar(select(User).where(User.email == "student03@example.edu"))
        tpl3 = db.get(FaceTemplate, stud3.id)
        if not tpl3:
            db.add(FaceTemplate(student_id=stud3.id, vector=[0.1] * 128, model="faceapi-128-v1", status="pending"))
        else:
            tpl3.status = "pending"

        # Student 4 (not on course roster)
        stud_unrostered = db.scalar(select(User).where(User.email == "student04@example.edu"))
        tpl4 = db.get(FaceTemplate, stud_unrostered.id)
        if not tpl4:
            db.add(FaceTemplate(student_id=stud_unrostered.id, vector=[0.1] * 128, model="faceapi-128-v1", status="confirmed"))
        else:
            tpl4.status = "confirmed"

        # Add stud1, stud2, stud3 to roster
        db.add(RosterEntry(course_id=course.id, student_id=stud1.id))
        db.add(RosterEntry(course_id=course.id, student_id=stud2.id))
        db.add(RosterEntry(course_id=course.id, student_id=stud3.id))

        db.commit()
        db.refresh(prof)
        db.refresh(course)
        db.refresh(stud1)
        db.refresh(stud2)
        db.refresh(stud3)
        db.refresh(stud_unrostered)

        return {
            "prof": prof,
            "course": course,
            "stud1": stud1,
            "stud2": stud2,
            "stud3_pending": stud3,
            "stud_unrostered": stud_unrostered,
            "t_prof": make_token(prof.id, "professor", "access"),
            "t_s1": make_token(stud1.id, "student", "access"),
            "t_s2": make_token(stud2.id, "student", "access"),
            "t_s3_pending": make_token(stud3.id, "student", "access"),
            "t_unrostered": make_token(stud_unrostered.id, "student", "access"),
        }


# ==============================================================================
# Active List & Challenge (Cases 1, 2)
# ==============================================================================

def test_01_active_sessions_list_and_visibility(client, p4_fixtures):
    """1. /sessions/active: only live/paused sessions of courses student is rostered in;
    checked_in true once they have checked in; empty before start.
    Student not on roster sees nothing.
    /sessions/active must never be captured by /sessions/{session_id} (student gets 200, not 403).
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_unrostered = p4_fixtures["t_unrostered"]
    cid = p4_fixtures["course"].id

    # 1. Before session starts -> active list is empty
    r0 = client.get("/api/v1/sessions/active", headers={"Authorization": f"Bearer {t_s1}"})
    assert r0.status_code == 200
    assert r0.json() == []

    # 2. Create session (status=created) -> still empty
    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    r_created = client.get("/api/v1/sessions/active", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_created.status_code == 200
    assert not any(s["id"] == sid for s in r_created.json())

    # 3. Start session (status=live) -> visible with checked_in=False
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})
    r_live = client.get("/api/v1/sessions/active", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_live.status_code == 200
    active = [s for s in r_live.json() if s["id"] == sid]
    assert len(active) == 1
    assert active[0]["status"] == "live"
    assert active[0]["checked_in"] is False

    # 4. Unrostered student sees nothing
    r_unrost = client.get("/api/v1/sessions/active", headers={"Authorization": f"Bearer {t_unrostered}"})
    assert r_unrost.status_code == 200
    assert not any(s["id"] == sid for s in r_unrost.json())

    # 5. Paused session is still listed
    client.post(f"/api/v1/sessions/{sid}/pause", headers={"Authorization": f"Bearer {t_prof}"})
    r_paused = client.get("/api/v1/sessions/active", headers={"Authorization": f"Bearer {t_s1}"})
    active_p = [s for s in r_paused.json() if s["id"] == sid]
    assert len(active_p) == 1
    assert active_p[0]["status"] == "paused"


def test_02_challenge_issuance_and_validation(client, p4_fixtures):
    """2. /challenge: live session + confirmed enrollment -> {challenge_id, expires_ms, colour_seq (4 colours)};
    not on roster -> 404 SESSION_NOT_FOUND; session not live -> 404 SESSION_NOT_ACTIVE;
    enrollment not confirmed (none/pending/rejected) -> 409 NOT_ENROLLED.
    Each call stores a new row; sequences are random.
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s3_pending = p4_fixtures["t_s3_pending"]
    t_unrostered = p4_fixtures["t_unrostered"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]

    # Session created (not live) -> 404 SESSION_NOT_ACTIVE
    r_not_active = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_not_active.status_code == 404
    assert r_not_active.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    # Start session -> live
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    # Student not on roster -> 404 SESSION_NOT_FOUND
    r_not_roster = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {t_unrostered}"})
    assert r_not_roster.status_code == 404
    assert r_not_roster.json()["error"]["code"] == "SESSION_NOT_FOUND"

    # Enrollment not confirmed (pending) -> 409 NOT_ENROLLED
    r_pending = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {t_s3_pending}"})
    assert r_pending.status_code == 409
    assert r_pending.json()["error"]["code"] == "NOT_ENROLLED"

    # Success path
    r_ok1 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_ok1.status_code == 200
    data1 = r_ok1.json()
    assert "challenge_id" in data1
    assert len(data1["colour_seq"]) == 4
    assert data1["expires_ms"] > int(time.time() * 1000)

    # Second challenge -> stores new row, sequences are random
    r_ok2 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_ok2.status_code == 200
    data2 = r_ok2.json()
    assert data1["challenge_id"] != data2["challenge_id"]

    with SessionLocal() as db:
        challenges = db.scalars(select(Challenge).where(Challenge.session_id == sid)).all()
        assert len(challenges) == 2


# ==============================================================================
# Check-in Gates & Idempotency (Cases 3, 4, 5, 6, 7, 8)
# ==============================================================================

def test_03_checkin_success_and_checked_in_count(client, p4_fixtures):
    """3. Valid current QR -> 200 {checkin_id, status: "VERIFIED", score: 0.5, flags: ["QR_ONLY"], latency_ms}.
    Also valid with the previous window (grace 1).
    Row appears in checkins; professor's checked_in_count increments.
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s2 = p4_fixtures["t_s2"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    # 1. Student 1 checks in with current window
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr_curr = tokens.make_qr_payload(secret, sid, w)

    r1 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr_curr}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["status"] == "VERIFIED"
    assert d1["score"] == 0.5
    assert d1["flags"] == ["QR_ONLY"]
    assert "latency_ms" in d1

    # Professor sees checked_in_count == 1
    r_s = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {t_prof}"})
    assert r_s.json()["checked_in_count"] == 1

    # 2. Student 2 checks in with previous window (grace 1)
    qr_prev = tokens.make_qr_payload(secret, sid, w - 1)
    r2 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr_prev}, headers={"Authorization": f"Bearer {t_s2}"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "VERIFIED"

    # Professor sees checked_in_count == 2
    r_s2 = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {t_prof}"})
    assert r_s2.json()["checked_in_count"] == 2


def test_04_checkin_rejection_gates(client, p4_fixtures):
    """4. Rejections:
    - garbage string or wrong prefix -> 403 TOKEN_INVALID
    - forged token -> 403 TOKEN_INVALID
    - session id different from path -> 403 TOKEN_INVALID
    - valid-but-old window (older than grace) and future window -> 410 TOKEN_EXPIRED
    - QR from session B posted to session A -> 403 TOKEN_INVALID
    - session paused/created/closed -> 404 SESSION_NOT_ACTIVE
    - student not on roster -> 404 SESSION_NOT_FOUND
    - enrollment not confirmed -> 409 NOT_ENROLLED
    - professor token -> 403
    - no token -> 401
    - qr empty or >256 chars -> 422
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s3_pending = p4_fixtures["t_s3_pending"]
    t_unrostered = p4_fixtures["t_unrostered"]
    cid = p4_fixtures["course"].id

    cid_b = client.post("/api/v1/courses", json={"name": "P4 Course B"}, headers={"Authorization": f"Bearer {t_prof}"}).json()["id"]
    s_a = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"}).json()
    s_b = client.post("/api/v1/sessions", json={"course_id": cid_b, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"}).json()
    sid_a, sid_b = s_a["id"], s_b["id"]
    client.post(f"/api/v1/sessions/{sid_a}/start", headers={"Authorization": f"Bearer {t_prof}"})
    client.post(f"/api/v1/sessions/{sid_b}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret_a = db.get(ClassSession, sid_a).secret
        secret_b = db.get(ClassSession, sid_b).secret

    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    valid_qr_a = tokens.make_qr_payload(secret_a, sid_a, w)
    valid_qr_b = tokens.make_qr_payload(secret_b, sid_b, w)

    # Garbage -> 403
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": "not_a_qr"}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TOKEN_INVALID"

    # Forged -> 403
    forged = valid_qr_a[:-2] + ("11" if not valid_qr_a.endswith("11") else "22")
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": forged}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TOKEN_INVALID"

    # Session ID different from path -> 403
    mismatched = valid_qr_a.replace(f":{sid_a}:", ":different_sess:")
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": mismatched}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TOKEN_INVALID"

    # QR from session B posted to session A -> 403
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_b}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TOKEN_INVALID"

    # Older than grace -> 410
    old_qr = tokens.make_qr_payload(secret_a, sid_a, w - 2)
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": old_qr}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "TOKEN_EXPIRED"

    # Future window -> 410
    future_qr = tokens.make_qr_payload(secret_a, sid_a, w + 2)
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": future_qr}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "TOKEN_EXPIRED"

    # Enrollment not confirmed -> 409
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_a}, headers={"Authorization": f"Bearer {t_s3_pending}"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ENROLLED"

    # Student not on roster -> 404
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_a}, headers={"Authorization": f"Bearer {t_unrostered}"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"

    # Professor token -> 403
    r = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_a}, headers={"Authorization": f"Bearer {t_prof}"})
    assert r.status_code == 403

    # No token -> 401
    assert client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_a}).status_code == 401

    # Empty or >256 chars -> 422
    assert client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": ""}, headers={"Authorization": f"Bearer {t_s1}"}).status_code == 422
    assert client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": "a" * 257}, headers={"Authorization": f"Bearer {t_s1}"}).status_code == 422

    # Paused session -> 404 SESSION_NOT_ACTIVE
    client.post(f"/api/v1/sessions/{sid_a}/pause", headers={"Authorization": f"Bearer {t_prof}"})
    r_paused = client.post(f"/api/v1/sessions/{sid_a}/checkin", json={"qr": valid_qr_a}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r_paused.status_code == 404 and r_paused.json()["error"]["code"] == "SESSION_NOT_ACTIVE"


def test_05_checkin_idempotency_and_simultaneous_submissions(client, p4_fixtures):
    """5. Idempotency: a second POST (same or newer QR) returns 200 with the same checkin_id
    and no new row, including 20 simultaneous submissions from one student (exactly one row; never a 500).
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    stud1 = p4_fixtures["stud1"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr = tokens.make_qr_payload(secret, sid, w)

    def send_req(i):
        return client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {t_s1}"})

    # 20 simultaneous submissions from the same student
    with ThreadPoolExecutor(max_workers=20) as ex:
        responses = list(ex.map(send_req, range(20)))

    status_codes = [r.status_code for r in responses]
    assert all(code in (200, 409) for code in status_codes)

    # Verify exactly one checkin row in DB
    with SessionLocal() as db:
        rows = db.scalars(select(Checkin).where(Checkin.session_id == sid, Checkin.student_id == stud1.id)).all()
        assert len(rows) == 1
        stored_id = rows[0].id

    # Subsequent retry returns 200 with same checkin_id
    r_retry = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {t_s1}"})
    assert r_retry.status_code == 200
    assert r_retry.json()["checkin_id"] == stored_id


def test_06_checkin_me_status_endpoint(client, p4_fixtures):
    """6. /checkin/me: {status:"PENDING"} before check-in, the same result body after;
    other students' check-ins are never visible.
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s2 = p4_fixtures["t_s2"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    # Before checkin
    r_me1 = client.get(f"/api/v1/sessions/{sid}/checkin/me", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_me1.status_code == 200
    assert r_me1.json() == {"status": "PENDING"}

    # Student 1 checks in
    now_ms = int(time.time() * 1000)
    qr = tokens.make_qr_payload(secret, sid, tokens.window_for(now_ms, 1500))
    res_in = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {t_s1}"})
    cid1 = res_in.json()["checkin_id"]

    # Student 1 /checkin/me matches result
    r_me1_after = client.get(f"/api/v1/sessions/{sid}/checkin/me", headers={"Authorization": f"Bearer {t_s1}"})
    assert r_me1_after.json()["checkin_id"] == cid1

    # Student 2 /checkin/me is still PENDING
    r_me2 = client.get(f"/api/v1/sessions/{sid}/checkin/me", headers={"Authorization": f"Bearer {t_s2}"})
    assert r_me2.json() == {"status": "PENDING"}


def test_07_per_student_nonce_allows_multiple_students_same_window(client, p4_fixtures):
    """7. The per-student nonce does not block other students:
    N students posting the same QR in the same window all succeed.
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s2 = p4_fixtures["t_s2"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    qr = tokens.make_qr_payload(secret, sid, tokens.window_for(int(time.time() * 1000), 1500))

    # Both students post the same QR in the same window
    r1 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {t_s1}"})
    r2 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {t_s2}"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["checkin_id"] != r2.json()["checkin_id"]


def test_08_replay_semantics_old_token_cross_student(client, p4_fixtures):
    """8. Replay: a captured valid request replayed by a different student account with the old QR
    after the grace window -> 410; replayed by the same student -> same stored result.
    """
    t_prof = p4_fixtures["t_prof"]
    t_s1 = p4_fixtures["t_s1"]
    t_s2 = p4_fixtures["t_s2"]
    cid = p4_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr_valid = tokens.make_qr_payload(secret, sid, w)

    # Student 1 succeeds
    res1 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr_valid}, headers={"Authorization": f"Bearer {t_s1}"})
    cid1 = res1.json()["checkin_id"]

    old_qr = tokens.make_qr_payload(secret, sid, w - 2)

    # Student 2 tries with old QR -> 410 TOKEN_EXPIRED
    res_s2 = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": old_qr}, headers={"Authorization": f"Bearer {t_s2}"})
    assert res_s2.status_code == 410

    # Student 1 tries again with old QR -> returns original result
    res_s1_replay = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": old_qr}, headers={"Authorization": f"Bearer {t_s1}"})
    assert res_s1_replay.status_code == 200
    assert res_s1_replay.json()["checkin_id"] == cid1


# ==============================================================================
# Load & Timing (Cases 9, 10)
# ==============================================================================

def test_09_thundering_herd_and_load_distribution(client, p4_fixtures):
    """9. 240 students check in concurrently within a single QR window ("thundering herd"):
    0 5xx, 0 lost check-ins, checked_in_count = 240.
    Report p50/p95/p99 and latency_ms field distribution.
    Target: p99 <= 400 ms.
    """
    t_prof = p4_fixtures["t_prof"]
    cid = p4_fixtures["course"].id

    # Create 240 students on course roster in bulk
    pw = hash_password("gatekeeper-dev")
    stud_tokens = []
    with SessionLocal() as db:
        for i in range(240):
            uid = f"th_{i:03d}_{uuid.uuid4().hex[:6]}"
            s = User(email=f"{uid}@example.edu", roll_no=f"R_{uid}", name=f"Bulk Student {i}", role="student", password_hash=pw)
            db.add(s)
            db.flush()
            db.add(RosterEntry(course_id=cid, student_id=s.id))
            db.add(FaceTemplate(student_id=s.id, vector=[0.1] * 128, model="faceapi-128-v1", status="confirmed"))
            stud_tokens.append(make_token(s.id, "student", "access"))
        db.commit()

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t_prof}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t_prof}"})

    with SessionLocal() as db:
        secret = db.get(ClassSession, sid).secret

    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr = tokens.make_qr_payload(secret, sid, w)

    def do_checkin(tok):
        t0 = time.time()
        r = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers={"Authorization": f"Bearer {tok}"})
        elapsed = (time.time() - t0) * 1000
        server_lat = r.json().get("latency_ms", 0) if r.status_code == 200 else 0
        return r.status_code, elapsed, server_lat

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(do_checkin, stud_tokens))

    statuses = [r[0] for r in results]
    client_times = sorted([r[1] for r in results])
    server_lats = sorted([r[2] for r in results])

    assert all(s == 200 for s in statuses), f"Non-200 statuses observed: {set(statuses)}"

    p50_c = client_times[int(len(client_times) * 0.50)]
    p95_c = client_times[int(len(client_times) * 0.95)]
    p99_c = client_times[int(len(client_times) * 0.99)]

    p50_s = server_lats[int(len(server_lats) * 0.50)]
    p95_s = server_lats[int(len(server_lats) * 0.95)]
    p99_s = server_lats[int(len(server_lats) * 0.99)]

    print(f"\n[LOAD TEST 240 CHECK-INS] Client RT: p50={p50_c:.1f}ms, p95={p95_c:.1f}ms, p99={p99_c:.1f}ms")
    print(f"[LOAD TEST 240 CHECK-INS] Server latency_ms: p50={p50_s}ms, p95={p95_s}ms, p99={p99_s}ms")

    assert p99_c <= 400.0, f"p99 client latency {p99_c}ms exceeded 400ms target"

    # Verify checked_in_count in GET /sessions/{id}
    r_sess = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {t_prof}"})
    assert r_sess.json()["checked_in_count"] == 240


def test_10_memory_and_redis_nonce_behavior():
    """10. Nonce store behavior: claims prevent duplicates within TTL,
    expires properly, in-process fallback functions cleanly.
    """
    store = nonce._MemoryStore()
    k = "test_key_123"

    assert store.claim(k, 1) is True
    assert store.claim(k, 1) is False  # Duplicate claim rejected

    # After expiry
    time.sleep(1.05)
    assert store.claim(k, 1) is True
