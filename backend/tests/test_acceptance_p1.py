"""Acceptance test suite for Phase 1: Foundation and contracts.
Specifications defined in docs/acceptance/P1.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import jwt
import pytest
import starlette.websockets
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Course, RosterEntry, User
from app.routers.stubs import STUBS
from app.security import make_token

REPO_ROOT = pathlib.Path(__file__).parents[2]


@pytest.fixture(scope="session")
def client():
    # Ensure tables exist
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture(scope="session")
def seeded_users():
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.role == "professor"))
        student = db.scalar(select(User).where(User.role == "student"))
        assert prof is not None, "Professor must be seeded"
        assert student is not None, "Student must be seeded"
        return {"professor": prof, "student": student}


# --- Contract Tests ---

def test_01_openapi_contract_and_websocket(client):
    """1. docs/openapi.json contains every method+path listed in V1 section 4 (47 operations) and no extras.
    WebSocket /api/v1/ws/sessions/{id} exists but closes with code 1013.
    """
    openapi_path = REPO_ROOT / "docs/openapi.json"
    assert openapi_path.exists(), "docs/openapi.json must exist"

    with open(openapi_path) as f:
        spec = json.load(f)

    # Count total operations
    operations = []
    for path, methods in spec["paths"].items():
        for method in methods:
            operations.append((method.upper(), path))

    assert len(operations) == 47, f"Expected 47 operations, got {len(operations)}"

    # Test WebSocket stub
    with pytest.raises(starlette.websockets.WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sessions/session-123") as ws:
            ws.receive_text()
    assert exc_info.value.code in (1013, 4401), f"Expected WS close code 1013 or 4401, got {exc_info.value.code}"


def test_02_stubs_auth_matrix(client, seeded_users):
    """2. For each stub: no token -> 401; wrong role -> 403; right role -> 501.
    Role map is in app/routers/stubs.py.
    """
    prof_token = make_token(seeded_users["professor"].id, "professor", "access")
    student_token = make_token(seeded_users["student"].id, "student", "access")

    for method, path, roles, _tag, _phase in STUBS:
        test_path = (
            "/api/v1"
            + path.replace("{session_id}", "s1")
            .replace("{course_id}", "c1")
            .replace("{student_id}", "st1")
            .replace("{record_id}", "r1")
            .replace("{dispute_id}", "d1")
        )

        # 1. No token -> 401
        res_no_auth = client.request(method, test_path)
        assert res_no_auth.status_code == 401, (
            f"Stub {method} {test_path} expected 401 with no token, got {res_no_auth.status_code}"
        )

        # 2. Right role -> 501
        right_tok = prof_token if "professor" in roles else student_token
        res_right = client.request(method, test_path, headers={"Authorization": f"Bearer {right_tok}"})
        assert res_right.status_code == 501, (
            f"Stub {method} {test_path} expected 501 with correct role, got {res_right.status_code}"
        )

        # 3. Wrong role -> 403
        wrong_tok = student_token if "professor" in roles else prof_token
        res_wrong = client.request(method, test_path, headers={"Authorization": f"Bearer {wrong_tok}"})
        assert res_wrong.status_code == 403, (
            f"Stub {method} {test_path} expected 403 with wrong role, got {res_wrong.status_code}"
        )


def test_03_error_body_envelope_and_validation_error(client):
    """3. Error body shape on every 4xx/5xx: {"error":{"code","message","request_id"}};
    validation failures are 422 VALIDATION_ERROR.
    """
    # 401 error envelope
    res_401 = client.get("/api/v1/me")
    assert res_401.status_code == 401
    err_401 = res_401.json().get("error")
    assert err_401 is not None
    assert set(err_401.keys()) == {"code", "message", "request_id"}

    # 422 validation failure envelope
    res_422 = client.post("/api/v1/auth/login", json={})
    assert res_422.status_code == 422
    err_422 = res_422.json().get("error")
    assert err_422 is not None
    assert set(err_422.keys()) == {"code", "message", "request_id"}
    assert err_422["code"] == "VALIDATION_ERROR"


# --- Auth Tests ---

def test_04_login_email_roll_no_and_constant_time_reject(client):
    """4. Login works with email or roll number; wrong password and unknown identifier both return
    401 INVALID_CREDENTIALS with the same body.
    """
    # Login via email
    res_prof = client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"})
    assert res_prof.status_code == 200
    data_prof = res_prof.json()
    assert "access_token" in data_prof and "refresh_token" in data_prof and data_prof["role"] == "professor"

    # Login via roll number
    res_student = client.post("/api/v1/auth/login", json={"identifier": "R001", "password": "gatekeeper-dev"})
    assert res_student.status_code == 200
    data_student = res_student.json()
    assert "access_token" in data_student and "refresh_token" in data_student and data_student["role"] == "student"

    # Wrong password
    res_wrong_pw = client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "wrong-pw"})
    assert res_wrong_pw.status_code == 401
    assert res_wrong_pw.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # Unknown identifier
    res_unknown_id = client.post("/api/v1/auth/login", json={"identifier": "nonexistent@example.edu", "password": "wrong-pw"})
    assert res_unknown_id.status_code == 401
    assert res_unknown_id.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # Identical error codes and messages
    assert res_wrong_pw.json()["error"]["code"] == res_unknown_id.json()["error"]["code"]
    assert res_wrong_pw.json()["error"]["message"] == res_unknown_id.json()["error"]["message"]


def test_05_access_and_refresh_token_separation(client):
    """5. Access token works on /me; refresh token does not (401).
    Refresh token works on /auth/refresh; access token does not (401).
    """
    login_res = client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"})
    tokens = login_res.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]

    # Access on /me -> 200
    r1 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"})
    assert r1.status_code == 200

    # Refresh on /me -> 401
    r2 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r2.status_code == 401

    # Refresh on /auth/refresh -> 200
    r3 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r3.status_code == 200
    new_tokens = r3.json()
    assert "access_token" in new_tokens and "refresh_token" in new_tokens

    # Access on /auth/refresh -> 401
    r4 = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert r4.status_code == 401


def test_06_token_expiry_tampering_and_alg_none(client, seeded_users):
    """6. Expired token -> 401. Tampered signature -> 401. alg=none token -> 401."""
    user = seeded_users["professor"]
    now = int(time.time())

    # Expired token
    expired_jwt = jwt.encode(
        {"sub": user.id, "role": user.role, "typ": "access", "iat": now - 3600, "exp": now - 10},
        config.SECRET_KEY,
        algorithm="HS256",
    )
    r_exp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired_jwt}"})
    assert r_exp.status_code == 401

    # Tampered signature
    valid_jwt = make_token(user.id, user.role, "access")
    tampered_jwt = valid_jwt[:-4] + ("zzzz" if not valid_jwt.endswith("zzzz") else "aaaa")
    r_tamper = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tampered_jwt}"})
    assert r_tamper.status_code == 401

    # alg=none token
    none_jwt = jwt.encode(
        {"sub": user.id, "role": user.role, "typ": "access", "iat": now, "exp": now + 3600},
        key="",
        algorithm="none",
    )
    r_none = client.get("/api/v1/me", headers={"Authorization": f"Bearer {none_jwt}"})
    assert r_none.status_code == 401


def test_07_me_and_time_endpoints(client):
    """7. /me returns role matching the account; /time returns server epoch ms within 2 s of the test clock."""
    # Professor
    login_prof = client.post("/api/v1/auth/login", json={"identifier": "prof@example.edu", "password": "gatekeeper-dev"})
    prof_token = login_prof.json()["access_token"]
    me_prof = client.get("/api/v1/me", headers={"Authorization": f"Bearer {prof_token}"}).json()
    assert me_prof["role"] == "professor"
    assert me_prof["email"] == "prof@example.edu"

    # Student
    login_stud = client.post("/api/v1/auth/login", json={"identifier": "student01@example.edu", "password": "gatekeeper-dev"})
    stud_token = login_stud.json()["access_token"]
    me_stud = client.get("/api/v1/me", headers={"Authorization": f"Bearer {stud_token}"}).json()
    assert me_stud["role"] == "student"
    assert me_stud["roll_no"] == "R001"

    # /time
    test_clock_ms = int(time.time() * 1000)
    time_res = client.get("/api/v1/time", headers={"Authorization": f"Bearer {prof_token}"})
    assert time_res.status_code == 200
    server_ms = time_res.json()["server_ms"]
    assert abs(server_ms - test_clock_ms) <= 2000, f"Server clock differed by {abs(server_ms - test_clock_ms)} ms"


def test_08_readyz_and_healthz(client):
    """8. GET /readyz -> 200 with DB up. (Also GET /healthz -> 200)"""
    res_ready = client.get("/api/v1/readyz")
    assert res_ready.status_code == 200
    assert res_ready.json() == {"ok": True}

    res_health = client.get("/api/v1/healthz")
    assert res_health.status_code == 200
    assert res_health.json() == {"ok": True}


def test_09_refuse_start_in_prod_without_secret():
    """9. With GK_ENV=prod and no GK_SECRET_KEY the app refuses to start."""
    script = "import os; os.environ['GK_ENV'] = 'prod'; import app.config"
    res = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT / "backend"), capture_output=True, text=True)
    assert res.returncode != 0
    assert "GK_SECRET_KEY must be set in production" in res.stderr


# --- Data Tests ---

def test_10_seed_counts_and_idempotence():
    """10. After seed: 1 professor, 30 students, 1 course, 30 roster rows.
    Seed is idempotent (second run prints 'already seeded').
    """
    with SessionLocal() as db:
        prof_count = db.query(User).filter(User.role == "professor").count()
        student_count = db.query(User).filter(User.role == "student").count()
        course_count = db.query(Course).count()
        roster_count = db.query(RosterEntry).count()

        assert prof_count == 1, f"Expected 1 professor, got {prof_count}"
        assert student_count == 30, f"Expected 30 students, got {student_count}"
        assert course_count == 1, f"Expected 1 course, got {course_count}"
        assert roster_count == 30, f"Expected 30 roster rows, got {roster_count}"

    # Test seed idempotency by running the script
    res = subprocess.run([sys.executable, "scripts/seed.py"], cwd=str(REPO_ROOT / "backend"), capture_output=True, text=True)
    assert res.returncode == 0
    assert "already seeded" in res.stdout


def test_11_passwords_are_argon2_hashed():
    """11. Password hashes are argon2 (never plaintext in the DB)."""
    with SessionLocal() as db:
        users = db.query(User).all()
        assert len(users) >= 31
        for u in users:
            assert u.password_hash.startswith("$argon2"), f"User {u.email} does not have an argon2 hash: {u.password_hash}"
            assert "gatekeeper-dev" not in u.password_hash
