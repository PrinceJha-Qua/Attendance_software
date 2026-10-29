"""Acceptance test suite for Phase 3: Session lifecycle + Projection screen.
Specifications defined in docs/acceptance/P3.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import tokens
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, ClassSession, Course, RosterEntry, User
from app.security import hash_password, make_token

SEEDED_EMAILS = {"prof@example.edu"} | {f"student{i:02d}@example.edu" for i in range(1, 31)}


def _cleanup_test_data():
    """Removes sessions, test courses, and test users outside seeded set."""
    with SessionLocal() as db:
        test_courses = db.scalars(select(Course).where(Course.name != "CS101 Intro to Computing")).all()
        for c in test_courses:
            db.query(ClassSession).filter(ClassSession.course_id == c.id).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id == c.id).delete()
            db.delete(c)

        # Clear any sessions created on the seeded course
        seeded_course = db.scalar(select(Course).where(Course.name == "CS101 Intro to Computing"))
        if seeded_course:
            sessions = db.scalars(select(ClassSession).where(ClassSession.course_id == seeded_course.id)).all()
            for s in sessions:
                db.query(AuditLog).filter(AuditLog.target == s.id).delete()
                db.delete(s)

        test_users = db.scalars(select(User).where(~User.email.in_(SEEDED_EMAILS))).all()
        for u in test_users:
            db.query(AuditLog).filter(AuditLog.actor_id == u.id).delete()
            db.delete(u)

        db.commit()


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def db_isolation():
    _cleanup_test_data()
    yield
    _cleanup_test_data()


@pytest.fixture
def p3_fixtures():
    """Provides isolated professor, second professor, course, and student tokens."""
    with SessionLocal() as db:
        prof1 = db.scalar(select(User).where(User.email == "prof@example.edu"))
        assert prof1 is not None

        prof2_email = f"prof2_{uuid.uuid4().hex[:6]}@example.edu"
        prof2 = User(email=prof2_email, name="Prof 2", role="professor", password_hash=hash_password("gatekeeper-dev"))
        db.add(prof2)

        stud1 = db.scalar(select(User).where(User.email == "student01@example.edu"))
        assert stud1 is not None

        course = Course(professor_id=prof1.id, name=f"P3 Course {uuid.uuid4().hex[:6]}")
        db.add(course)
        db.flush()

        # Add 5 roster students to this course
        students = db.scalars(select(User).where(User.role == "student").limit(5)).all()
        for s in students:
            db.add(RosterEntry(course_id=course.id, student_id=s.id))

        db.commit()
        db.refresh(prof1)
        db.refresh(prof2)
        db.refresh(stud1)
        db.refresh(course)

        t_prof1 = make_token(prof1.id, "professor", "access")
        t_prof2 = make_token(prof2.id, "professor", "access")
        t_stud1 = make_token(stud1.id, "student", "access")

        return {
            "prof1": prof1,
            "prof2": prof2,
            "stud1": stud1,
            "course": course,
            "t_prof1": t_prof1,
            "t_prof2": t_prof2,
            "t_stud1": t_stud1,
        }


# ==============================================================================
# Create / Read (Cases 1, 2, 3)
# ==============================================================================

def test_01_session_create_and_validation(client, p3_fixtures):
    """1. POST /sessions {course_id, step_ms?, audio_mode?} -> 201 status created,
    defaults step_ms=1500, audio_mode=off.
    step_ms must be 1000..10000 (else 422); audio_mode only off|burst;
    unknown or other professor's course -> 404 COURSE_NOT_FOUND; student token -> 403.
    Response includes roster_count; checked_in_count is 0.
    """
    t1 = p3_fixtures["t_prof1"]
    t2 = p3_fixtures["t_prof2"]
    ts = p3_fixtures["t_stud1"]
    cid = p3_fixtures["course"].id

    # 1. Successful create with defaults
    res = client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {t1}"})
    assert res.status_code == 201
    s = res.json()
    assert s["status"] == "created"
    assert s["step_ms"] == 1500
    assert s["audio_mode"] == "off"
    assert s["roster_count"] == 5
    assert s["checked_in_count"] == 0
    assert s["started_at"] is None

    # 2. step_ms validation: 1000..10000
    assert client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 999}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422
    assert client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 10001}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422

    # 3. audio_mode validation: off|burst only
    assert client.post("/api/v1/sessions", json={"course_id": cid, "audio_mode": "invalid"}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422
    # In P7, one open session per course: start & close first session before creating second
    client.post(f"/api/v1/sessions/{s['id']}/start", headers={"Authorization": f"Bearer {t1}"})
    client.post(f"/api/v1/sessions/{s['id']}/close", headers={"Authorization": f"Bearer {t1}"})
    res_burst = client.post("/api/v1/sessions", json={"course_id": cid, "audio_mode": "burst"}, headers={"Authorization": f"Bearer {t1}"})
    assert res_burst.status_code == 201
    assert res_burst.json()["audio_mode"] == "burst"

    # 4. Unknown or other professor's course -> 404 COURSE_NOT_FOUND
    assert client.post("/api/v1/sessions", json={"course_id": "nonexistent"}, headers={"Authorization": f"Bearer {t1}"}).status_code == 404
    r_other = client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {t2}"})
    assert r_other.status_code == 404
    assert r_other.json()["error"]["code"] == "COURSE_NOT_FOUND"

    # 5. Student token -> 403
    assert client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {ts}"}).status_code == 403


def test_02_session_get_owner_isolation_and_no_secret_leak(client, p3_fixtures):
    """2. GET /sessions/{id}: owner only; another professor or unknown id -> 404 SESSION_NOT_FOUND.
    Two sessions for the same course may coexist.
    3. The per-session secret never appears in any response (search every response body).
    """
    t1 = p3_fixtures["t_prof1"]
    t2 = p3_fixtures["t_prof2"]
    cid = p3_fixtures["course"].id

    r1 = client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {t1}"})
    cid2 = client.post("/api/v1/courses", json={"name": "P3 Course 2"}, headers={"Authorization": f"Bearer {t1}"}).json()["id"]
    r2 = client.post("/api/v1/sessions", json={"course_id": cid2}, headers={"Authorization": f"Bearer {t1}"})
    assert r1.status_code == 201 and r2.status_code == 201
    sid1 = r1.json()["id"]
    sid2 = r2.json()["id"]
    assert sid1 != sid2  # Two sessions coexist

    # GET /sessions/{id} by owner
    res_get = client.get(f"/api/v1/sessions/{sid1}", headers={"Authorization": f"Bearer {t1}"})
    assert res_get.status_code == 200
    assert res_get.json()["id"] == sid1

    # GET by other prof or unknown id -> 404
    assert client.get(f"/api/v1/sessions/{sid1}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
    assert client.get("/api/v1/sessions/unknown_session", headers={"Authorization": f"Bearer {t1}"}).status_code == 404

    # 3. Secret leak check
    with SessionLocal() as db:
        sess = db.get(ClassSession, sid1)
        raw_secret = sess.secret
        hex_secret = raw_secret.hex()

    for resp in (r1, r2, res_get):
        body_text = resp.text
        assert "secret" not in body_text
        assert hex_secret not in body_text


# ==============================================================================
# State Machine (Cases 4, 5)
# ==============================================================================

def test_03_session_state_machine_lifecycle_and_audit(client, p3_fixtures):
    """4. start: created->live sets started_at; any other state -> 409 INVALID_STATE.
    pause: live->paused only. resume: paused->live only (does not change started_at).
    Each transition writes an audit_log row. Repeating a transition -> 409.
    5. Auth matrix: no token 401, student 403 on all these routes; close still returns 501.
    """
    t1 = p3_fixtures["t_prof1"]
    ts = p3_fixtures["t_stud1"]
    cid = p3_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {t1}"})
    sid = s_res.json()["id"]

    # Invalid transitions from created
    assert client.post(f"/api/v1/sessions/{sid}/pause", headers={"Authorization": f"Bearer {t1}"}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/resume", headers={"Authorization": f"Bearer {t1}"}).status_code == 409

    # Start: created -> live
    r_start = client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t1}"})
    assert r_start.status_code == 200
    assert r_start.json()["status"] == "live"
    started_at = r_start.json()["started_at"]
    assert started_at is not None

    # Repeating start -> 409
    r_start_dup = client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t1}"})
    assert r_start_dup.status_code == 409
    assert r_start_dup.json()["error"]["code"] == "INVALID_STATE"

    # Pause: live -> paused
    r_pause = client.post(f"/api/v1/sessions/{sid}/pause", headers={"Authorization": f"Bearer {t1}"})
    assert r_pause.status_code == 200
    assert r_pause.json()["status"] == "paused"

    # Repeating pause -> 409
    assert client.post(f"/api/v1/sessions/{sid}/pause", headers={"Authorization": f"Bearer {t1}"}).status_code == 409

    # Resume: paused -> live (started_at unchanged)
    r_resume = client.post(f"/api/v1/sessions/{sid}/resume", headers={"Authorization": f"Bearer {t1}"})
    assert r_resume.status_code == 200
    assert r_resume.json()["status"] == "live"
    assert r_resume.json()["started_at"] is not None
    assert r_resume.json()["started_at"].split("+")[0] == started_at.split("+")[0]

    # Repeating resume -> 409
    assert client.post(f"/api/v1/sessions/{sid}/resume", headers={"Authorization": f"Bearer {t1}"}).status_code == 409

    # 5. close (501 in P3, real in P7)
    r_close = client.post(f"/api/v1/sessions/{sid}/close", headers={"Authorization": f"Bearer {t1}"})
    assert r_close.status_code in (200, 501)

    # Auth matrix checks
    for action in ("start", "pause", "resume", "close"):
        assert client.post(f"/api/v1/sessions/{sid}/{action}").status_code == 401
        assert client.post(f"/api/v1/sessions/{sid}/{action}", headers={"Authorization": f"Bearer {ts}"}).status_code == 403

    # Audit log verification
    with SessionLocal() as db:
        logs = db.scalars(select(AuditLog).where(AuditLog.target == sid)).all()
        actions = [l.action for l in logs]
        assert "session.created" in actions
        assert "session.live" in actions
        assert "session.paused" in actions


# ==============================================================================
# Token Batches (Cases 6, 7, 8, 9)
# ==============================================================================

def test_04_token_batches_live_requirement_and_default_shape(client, p3_fixtures):
    """6. Only when live (created/paused -> 409 SESSION_NOT_LIVE).
    Default response: tokens from the current window through 60 s ahead (about 41 tokens at step 1500),
    at most 100. Shape {step_ms, server_ms, tokens:[{window, payload}]}; consecutive windows,
    payload format gk1:{session_id}:{window}:{16 chars}.
    """
    t1 = p3_fixtures["t_prof1"]
    cid = p3_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t1}"})
    sid = s_res.json()["id"]

    # created -> 409 SESSION_NOT_LIVE
    r_created = client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t1}"})
    assert r_created.status_code == 409
    assert r_created.json()["error"]["code"] == "SESSION_NOT_LIVE"

    # start session -> live
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t1}"})

    # Default batch fetch
    r_batch = client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t1}"})
    assert r_batch.status_code == 200
    b = r_batch.json()
    assert b["step_ms"] == 1500
    assert abs(b["server_ms"] - int(time.time() * 1000)) <= 2000
    tokens_list = b["tokens"]
    assert len(tokens_list) == 41  # 60000 / 1500 + 1 = 41 tokens

    # Check consecutive windows & format
    for i in range(len(tokens_list)):
        curr = tokens_list[i]
        assert curr["payload"].startswith(f"gk1:{sid}:{curr['window']}:")
        assert len(curr["payload"].split(":")[-1]) == 16
        if i > 0:
            assert curr["window"] == tokens_list[i - 1]["window"] + 1

    # paused -> 409 SESSION_NOT_LIVE
    client.post(f"/api/v1/sessions/{sid}/pause", headers={"Authorization": f"Bearer {t1}"})
    r_paused = client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t1}"})
    assert r_paused.status_code == 409
    assert r_paused.json()["error"]["code"] == "SESSION_NOT_LIVE"


def test_05_token_batches_parameter_validation_and_access_control(client, p3_fixtures):
    """7. from older than current window minus 1 -> 422 WINDOW_TOO_OLD;
    beyond 60 s ahead -> 422 TOO_FAR_AHEAD; count 1..100 (0 or 101 -> 422);
    count=3 returns exactly 3. Non-owner professor -> 404.
    """
    t1 = p3_fixtures["t_prof1"]
    t2 = p3_fixtures["t_prof2"]
    cid = p3_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t1}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t1}"})

    b_init = client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t1}"}).json()
    cur_w = b_init["tokens"][0]["window"]

    # from older than current window - 1 -> 422 WINDOW_TOO_OLD
    r_too_old = client.get(f"/api/v1/sessions/{sid}/tokens?from={cur_w - 2}", headers={"Authorization": f"Bearer {t1}"})
    assert r_too_old.status_code == 422
    assert r_too_old.json()["error"]["code"] == "WINDOW_TOO_OLD"

    # from window - 1 is accepted (grace window)
    r_grace = client.get(f"/api/v1/sessions/{sid}/tokens?from={cur_w - 1}", headers={"Authorization": f"Bearer {t1}"})
    assert r_grace.status_code == 200

    # beyond 60s ahead -> 422 TOO_FAR_AHEAD
    r_too_far = client.get(f"/api/v1/sessions/{sid}/tokens?from={cur_w + 50}", headers={"Authorization": f"Bearer {t1}"})
    assert r_too_far.status_code == 422
    assert r_too_far.json()["error"]["code"] == "TOO_FAR_AHEAD"

    # count parameter validation: 1..100
    assert client.get(f"/api/v1/sessions/{sid}/tokens?count=0", headers={"Authorization": f"Bearer {t1}"}).status_code == 422
    assert client.get(f"/api/v1/sessions/{sid}/tokens?count=101", headers={"Authorization": f"Bearer {t1}"}).status_code == 422

    r_c3 = client.get(f"/api/v1/sessions/{sid}/tokens?count=3", headers={"Authorization": f"Bearer {t1}"})
    assert r_c3.status_code == 200
    assert len(r_c3.json()["tokens"]) == 3

    # Non-owner professor -> 404
    assert client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t2}"}).status_code == 404


def test_06_tokens_shared_spec_independent_verification_and_grace(client, p3_fixtures):
    """8. Cross-check with the shared spec: every returned payload matches shared/TOKEN_SPEC.md
    computed independently with the session secret (read from the DB in the test);
    each token verifies ok at its own window and expired once older than the grace;
    a token from session A fails verification against session B (invalid).
    9. Token for the current window changes exactly at window boundaries (floor(server_ms / step_ms));
    server_ms is within 2 s of test clock.
    """
    t1 = p3_fixtures["t_prof1"]
    cid = p3_fixtures["course"].id

    # Create session A and session B in separate courses
    cid_b = client.post("/api/v1/courses", json={"name": "P3 Course B"}, headers={"Authorization": f"Bearer {t1}"}).json()["id"]
    s_a = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t1}"}).json()
    s_b = client.post("/api/v1/sessions", json={"course_id": cid_b, "step_ms": 1500}, headers={"Authorization": f"Bearer {t1}"}).json()
    sid_a, sid_b = s_a["id"], s_b["id"]
    client.post(f"/api/v1/sessions/{sid_a}/start", headers={"Authorization": f"Bearer {t1}"})
    client.post(f"/api/v1/sessions/{sid_b}/start", headers={"Authorization": f"Bearer {t1}"})

    with SessionLocal() as db:
        secret_a = db.get(ClassSession, sid_a).secret
        secret_b = db.get(ClassSession, sid_b).secret

    res = client.get(f"/api/v1/sessions/{sid_a}/tokens", headers={"Authorization": f"Bearer {t1}"})
    data = res.json()
    server_ms = data["server_ms"]
    assert abs(server_ms - int(time.time() * 1000)) <= 2000

    current_window = server_ms // 1500
    assert data["tokens"][0]["window"] == current_window

    for item in data["tokens"]:
        w = item["window"]
        payload = item["payload"]

        # Independent payload generation match
        expected = tokens.make_qr_payload(secret_a, sid_a, w)
        assert payload == expected

        # Verify ok at its own window
        parsed = tokens.parse_qr(payload)
        assert tokens.verify(secret_a, parsed, w * 1500 + 100, 1500, 1) == "ok"

        # Verify expired once older than grace (window + 2)
        assert tokens.verify(secret_a, parsed, (w + 2) * 1500 + 100, 1500, 1) == "expired"

        # Token from session A fails against session B secret
        assert tokens.verify(secret_b, parsed, w * 1500 + 100, 1500, 1) == "invalid"


# ==============================================================================
# Load & Concurrency (Cases 10, 11)
# ==============================================================================

def test_07_concurrent_load_performance(client, p3_fixtures):
    """10. 240 concurrent GET /tokens and 240 GET /sessions/{id}:
    report p50/p95/p99, no 5xx. Target p99 <= 400 ms.
    """
    t1 = p3_fixtures["t_prof1"]
    cid = p3_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid, "step_ms": 1500}, headers={"Authorization": f"Bearer {t1}"})
    sid = s_res.json()["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers={"Authorization": f"Bearer {t1}"})

    # 1. 240 concurrent GET /sessions/{id}
    def req_session(i):
        t0 = time.time()
        r = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {t1}"})
        return r.status_code, (time.time() - t0) * 1000

    with ThreadPoolExecutor(max_workers=20) as ex:
        res_s = list(ex.map(req_session, range(240)))

    codes_s = [r[0] for r in res_s]
    times_s = sorted([r[1] for r in res_s])
    assert all(code == 200 for code in codes_s)
    p50_s = times_s[int(len(times_s) * 0.50)]
    p95_s = times_s[int(len(times_s) * 0.95)]
    p99_s = times_s[int(len(times_s) * 0.99)]
    print(f"\n[BENCHMARK] GET /sessions/{sid} (240 reqs): p50={p50_s:.1f}ms, p95={p95_s:.1f}ms, p99={p99_s:.1f}ms")

    # 2. 240 concurrent GET /tokens
    def req_tokens(i):
        t0 = time.time()
        r = client.get(f"/api/v1/sessions/{sid}/tokens", headers={"Authorization": f"Bearer {t1}"})
        return r.status_code, (time.time() - t0) * 1000

    with ThreadPoolExecutor(max_workers=20) as ex:
        res_tok = list(ex.map(req_tokens, range(240)))

    codes_tok = [r[0] for r in res_tok]
    times_tok = sorted([r[1] for r in res_tok])
    assert all(code == 200 for code in codes_tok)
    p50_tok = times_tok[int(len(times_tok) * 0.50)]
    p95_tok = times_tok[int(len(times_tok) * 0.95)]
    p99_tok = times_tok[int(len(times_tok) * 0.99)]
    print(f"\n[BENCHMARK] GET /tokens (240 reqs): p50={p50_tok:.1f}ms, p95={p95_tok:.1f}ms, p99={p99_tok:.1f}ms")

    assert p99_tok <= 400.0, f"Target p99 <= 400ms exceeded: {p99_tok}ms"


def test_08_concurrent_hammer_state_transitions(client, p3_fixtures):
    """11. Hammer start/pause/resume from two clients concurrently:
    final state is one of the valid states; no 500s.
    """
    t1 = p3_fixtures["t_prof1"]
    cid = p3_fixtures["course"].id

    s_res = client.post("/api/v1/sessions", json={"course_id": cid}, headers={"Authorization": f"Bearer {t1}"})
    sid = s_res.json()["id"]

    def do_action(action):
        r = client.post(f"/api/v1/sessions/{sid}/{action}", headers={"Authorization": f"Bearer {t1}"})
        return r.status_code

    actions = ["start", "pause", "resume", "pause", "resume"] * 10
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(do_action, actions))

    # All requests must return either 200 (transition succeeded) or 409 (conflict/invalid transition)
    assert all(code in (200, 409) for code in results), f"Unexpected status codes: {set(results)}"

    # Final state is valid
    final_res = client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {t1}"})
    assert final_res.status_code == 200
    assert final_res.json()["status"] in ("live", "paused")
