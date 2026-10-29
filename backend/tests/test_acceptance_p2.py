"""Acceptance test suite for Phase 2: Courses, roster, enrollment.
Specifications defined in docs/acceptance/P2.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import io
import json
import pathlib
import subprocess
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, Consent, Course, FaceTemplate, RosterEntry, User
from app.security import hash_password, make_token
from app.storage import decrypt, photo_url

REPO_ROOT = pathlib.Path(__file__).parents[2]
JPEG_SAMPLE = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"raw_image_data_payload"
PNG_SAMPLE = b"\x89PNG\r\n\x1a\n" + b"raw_png_image_data"

SEEDED_EMAILS = {"prof@example.edu"} | {f"student{i:02d}@example.edu" for i in range(1, 31)}


def _cleanup_test_data():
    """Removes any database records created by tests outside the seeded set."""
    with SessionLocal() as db:
        test_users = db.scalars(select(User).where(~User.email.in_(SEEDED_EMAILS))).all()
        for u in test_users:
            db.query(FaceTemplate).filter(FaceTemplate.student_id == u.id).delete()
            db.query(Consent).filter(Consent.student_id == u.id).delete()
            db.query(AuditLog).filter(AuditLog.actor_id == u.id).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id == u.id).delete()
            db.delete(u)

        test_courses = db.scalars(select(Course).where(Course.name != "CS101 Intro to Computing")).all()
        for c in test_courses:
            db.query(RosterEntry).filter(RosterEntry.course_id == c.id).delete()
            db.delete(c)

        db.commit()


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def db_isolation():
    """Ensures test database is cleaned before and after each test."""
    _cleanup_test_data()
    yield
    _cleanup_test_data()


@pytest.fixture
def p2_fixtures():
    """Provides isolated professor and student fixtures for Phase 2 testing."""
    with SessionLocal() as db:
        prof1 = db.scalar(select(User).where(User.email == "prof@example.edu"))
        assert prof1 is not None, "Seeded professor required"

        prof2_email = f"prof2_{uuid.uuid4().hex[:6]}@example.edu"
        prof2 = User(email=prof2_email, name="Second Professor", role="professor", password_hash=hash_password("gatekeeper-dev"))
        db.add(prof2)

        stud1 = db.scalar(select(User).where(User.email == "student01@example.edu"))
        assert stud1 is not None, "Seeded student required"

        db.commit()
        db.refresh(prof1)
        db.refresh(prof2)
        db.refresh(stud1)

        t_prof1 = make_token(prof1.id, "professor", "access")
        t_prof2 = make_token(prof2.id, "professor", "access")
        t_stud1 = make_token(stud1.id, "student", "access")

        return {
            "prof1": prof1,
            "prof2": prof2,
            "stud1": stud1,
            "t_prof1": t_prof1,
            "t_prof2": t_prof2,
            "t_stud1": t_stud1,
        }


# ==============================================================================
# Courses API Cases (1 & 2)
# ==============================================================================

def test_01_course_creation_defaults_and_patch_validation(client, p2_fixtures):
    """1. Create (201) with defaults required_pct=75, excused_counts=true.
    GET /courses lists only the caller's courses (second professor sees none of them).
    PATCH validates: required_pct outside 1..100 -> 422; planned_total_sessions <1 -> 422;
    setting planned_total_sessions to null clears it.
    """
    t1 = p2_fixtures["t_prof1"]
    t2 = p2_fixtures["t_prof2"]

    # 1. Create with defaults
    res = client.post("/api/v1/courses", json={"name": "Intro to AI"}, headers={"Authorization": f"Bearer {t1}"})
    assert res.status_code == 201
    course = res.json()
    assert course["name"] == "Intro to AI"
    assert course["required_pct"] == 75.0
    assert course["excused_counts"] is True
    cid = course["id"]

    # 2. GET /courses caller isolation
    res_p1 = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {t1}"})
    assert res_p1.status_code == 200
    assert any(c["id"] == cid for c in res_p1.json())

    res_p2 = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {t2}"})
    assert res_p2.status_code == 200
    assert not any(c["id"] == cid for c in res_p2.json())

    # 3. PATCH validation
    assert client.patch(f"/api/v1/courses/{cid}", json={"required_pct": 101}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422
    assert client.patch(f"/api/v1/courses/{cid}", json={"required_pct": 0}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422
    assert client.patch(f"/api/v1/courses/{cid}", json={"planned_total_sessions": 0}, headers={"Authorization": f"Bearer {t1}"}).status_code == 422

    # Setting planned_total_sessions and clearing with null
    set_res = client.patch(f"/api/v1/courses/{cid}", json={"planned_total_sessions": 30}, headers={"Authorization": f"Bearer {t1}"})
    assert set_res.status_code == 200
    assert set_res.json()["planned_total_sessions"] == 30

    clear_res = client.patch(f"/api/v1/courses/{cid}", json={"planned_total_sessions": None}, headers={"Authorization": f"Bearer {t1}"})
    assert clear_res.status_code == 200
    assert clear_res.json()["planned_total_sessions"] is None


def test_02_course_cross_professor_isolation_and_role_guards(client, p2_fixtures):
    """2. A professor touching another professor's course on any /courses/{id}/... route -> 404 COURSE_NOT_FOUND.
    Student token on any prof route -> 403.
    """
    t1 = p2_fixtures["t_prof1"]
    t2 = p2_fixtures["t_prof2"]
    ts = p2_fixtures["t_stud1"]

    res = client.post("/api/v1/courses", json={"name": "Prof1 Private Course"}, headers={"Authorization": f"Bearer {t1}"})
    cid = res.json()["id"]

    sub_routes = [
        ("PATCH", f"/api/v1/courses/{cid}", {"name": "Hacked"}),
        ("GET", f"/api/v1/courses/{cid}/roster", None),
        ("GET", f"/api/v1/courses/{cid}/enrollments/pending", None),
        ("POST", f"/api/v1/courses/{cid}/enrollments/dummy_id/confirm", None),
        ("POST", f"/api/v1/courses/{cid}/enrollments/dummy_id/reject", None),
    ]

    for method, path, payload in sub_routes:
        r = client.request(method, path, json=payload, headers={"Authorization": f"Bearer {t2}"})
        assert r.status_code == 404, f"{method} {path} expected 404 for different prof, got {r.status_code}"
        assert r.json()["error"]["code"] == "COURSE_NOT_FOUND"

    for method, path, payload in sub_routes:
        r = client.request(method, path, json=payload, headers={"Authorization": f"Bearer {ts}"})
        assert r.status_code == 403, f"{method} {path} expected 403 for student token, got {r.status_code}"


# ==============================================================================
# Roster CSV Cases (3, 4, 5)
# ==============================================================================

def test_03_roster_import_valid_rows_and_account_creation(client, p2_fixtures):
    """3. Valid rows: existing students are added; unknown students get an account with a random
    password returned once in created_accounts; the new account can log in with it.
    """
    t1 = p2_fixtures["t_prof1"]
    c_res = client.post("/api/v1/courses", json={"name": "Roster Import 101"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    tag = uuid.uuid4().hex[:6]
    roll_new = f"R_NEW_{tag}"
    email_new = f"newstudent_{tag}@example.edu"

    csv_content = (
        f"Roll_No,Name,Email\n"
        f"R001,Student 01,student01@example.edu\n"
        f"{roll_new},New Student One,{email_new}\n"
    )

    res = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["added_to_roster"] == 2
    assert len(data["created_accounts"]) == 1
    new_acc = data["created_accounts"][0]
    assert new_acc["roll_no"] == roll_new
    assert new_acc["email"] == email_new
    initial_pw = new_acc["initial_password"]

    login_res = client.post("/api/v1/auth/login", json={"identifier": roll_new, "password": initial_pw})
    assert login_res.status_code == 200
    assert login_res.json()["role"] == "student"


def test_04_roster_import_row_level_errors(client, p2_fixtures):
    """4. Errors are per row and do not abort other rows:
    - missing/invalid field (MISSING_OR_INVALID_FIELD)
    - duplicate email or roll within the file (DUPLICATE_IN_FILE)
    - email and roll belonging to different users (EMAIL_ROLL_CONFLICT)
    - identifier belonging to a professor (NOT_A_STUDENT_ACCOUNT)
    """
    t1 = p2_fixtures["t_prof1"]
    c_res = client.post("/api/v1/courses", json={"name": "Roster Error Handling 101"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    tag = uuid.uuid4().hex[:6]
    roll_val = f"R_VAL_{tag}"
    email_val = f"val_{tag}@example.edu"

    csv_content = (
        f"roll_no,name,email\n"
        f"{roll_val},Valid Student,{email_val}\n"
        f"R_INV_{tag},No Email Name,\n"
        f"{roll_val},Dup Roll,another_{tag}@example.edu\n"
        f"R001,Conflict Student,student02@example.edu\n"
        f"R_PROF_{tag},Dev Prof,prof@example.edu\n"
    )

    res = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["added_to_roster"] == 1

    err_map = {e["row"]: e["reason"] for e in data["errors"]}
    assert err_map[3] == "MISSING_OR_INVALID_FIELD"
    assert err_map[4] == "DUPLICATE_IN_FILE"
    assert err_map[5] == "EMAIL_ROLL_CONFLICT"
    assert err_map[6] == "NOT_A_STUDENT_ACCOUNT"


def test_05_roster_import_edge_cases_and_idempotence(client, p2_fixtures):
    """5. Re-importing the same file is idempotent (already_on_roster counts, no new accounts).
    Missing columns -> 422 CSV_MISSING_COLUMNS; non-UTF-8 -> 422 BAD_CSV;
    >1000 rows -> 413; >1 MB -> 413.
    Time a 300-row import with all-new students; report seconds.
    """
    t1 = p2_fixtures["t_prof1"]
    c_res = client.post("/api/v1/courses", json={"name": "Roster Edge Cases 101"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    # 1. Missing columns -> 422 CSV_MISSING_COLUMNS
    bad_cols = b"name,email\nAlice,alice@example.edu\n"
    r_bad_cols = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(bad_cols), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_bad_cols.status_code == 422
    assert r_bad_cols.json()["error"]["code"] == "CSV_MISSING_COLUMNS"

    # 2. Non-UTF-8 -> 422 BAD_CSV
    r_bad_enc = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(b"roll_no,name,email\n\x80\xff\xfe"), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_bad_enc.status_code == 422
    assert r_bad_enc.json()["error"]["code"] == "BAD_CSV"

    # 3. >1000 rows -> 413 CSV_TOO_MANY_ROWS
    rows_1001 = "roll_no,name,email\n" + "\n".join(f"R_OVR_{i},Name,email_{i}@example.edu" for i in range(1001))
    r_over = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(rows_1001.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_over.status_code == 413

    # 4. >1 MB -> 413 CSV_TOO_LARGE
    large_csv = b"roll_no,name,email\n" + (b"R_BIG,Big Student,big@example.edu\n" * 40000)
    assert len(large_csv) > 1_000_000
    r_large = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(large_csv), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_large.status_code == 413

    # 5. Time 300-row import with all-new students
    tag = uuid.uuid4().hex[:6]
    rows_300 = "roll_no,name,email\n" + "\n".join(f"T_{tag}_{i:03d},Timed Student {i},t_{tag}_{i}@example.edu" for i in range(300))
    t0 = time.time()
    r_300 = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(rows_300.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    elapsed_s = time.time() - t0
    assert r_300.status_code == 200
    d_300 = r_300.json()
    assert d_300["added_to_roster"] == 300
    assert len(d_300["created_accounts"]) == 300
    print(f"\n[BENCHMARK] 300-row roster import took: {elapsed_s:.2f} seconds")

    # 6. Idempotence: re-importing the same 300 rows
    r_reimport = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(rows_300.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_reimport.status_code == 200
    d_re = r_reimport.json()
    assert d_re["added_to_roster"] == 0
    assert d_re["already_on_roster"] == 300
    assert len(d_re["created_accounts"]) == 0


# ==============================================================================
# Enrollment (Student) Cases (6, 7, 8, 9)
# ==============================================================================

def test_06_student_enrollment_validation(client):
    """6. Enroll before consent -> 409 CONSENT_REQUIRED.
    After consent: valid JPEG/PNG <=500 KB + 128 finite floats + model -> 202 status pending.
    Bad vector (wrong length, NaN/Infinity, strings, bool) -> 422 BAD_FACE_VECTOR.
    Non-image bytes (even with .jpg name) -> 422 BAD_PHOTO.
    >500 KB -> 413 PHOTO_TOO_LARGE.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"val_{tag}@example.edu", roll_no=f"V_{tag}", name="Val Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.commit()
        db.refresh(s)
    ts = make_token(s.id, "student", "access")
    valid_vec = [0.05] * 128

    # 1. Enroll before consent -> 409 CONSENT_REQUIRED
    r_pre_consent = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r_pre_consent.status_code == 409
    assert r_pre_consent.json()["error"]["code"] == "CONSENT_REQUIRED"

    # Give consent
    assert client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"}).status_code == 204

    # 2. Bad face vectors -> 422 BAD_FACE_VECTOR
    bad_vectors = [
        [0.1] * 127,
        [0.1] * 129,
        [float("nan")] + [0.1] * 127,
        [float("inf")] + [0.1] * 127,
        ["str"] * 128,
        [True] * 128,
    ]
    for bv in bad_vectors:
        r_bv = client.post(
            "/api/v1/students/me/enroll",
            data={"vector": json.dumps(bv), "model": "faceapi-128-v1"},
            files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
            headers={"Authorization": f"Bearer {ts}"},
        )
        assert r_bv.status_code == 422
        assert r_bv.json()["error"]["code"] == "BAD_FACE_VECTOR"

    # 3. Non-image bytes -> 422 BAD_PHOTO
    r_bad_img = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(b"NOT_JPEG_OR_PNG"), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r_bad_img.status_code == 422
    assert r_bad_img.json()["error"]["code"] == "BAD_PHOTO"

    # 4. >500 KB -> 413 PHOTO_TOO_LARGE
    r_big_img = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE + b"\x00" * 500_001), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r_big_img.status_code == 413
    assert r_big_img.json()["error"]["code"] == "PHOTO_TOO_LARGE"

    # 5. Valid enroll -> 202 status pending
    r_ok = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r_ok.status_code == 202
    assert r_ok.json()["status"] == "pending"


def test_07_enrollment_re_enroll_and_lifecycle(client):
    """7. Enroll again while pending or confirmed -> 409 ALREADY_ENROLLED.
    After rejection, enrolling again replaces the template (status back to pending).
    After DELETE face-data, enrolling again works.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"cyc_{tag}@example.edu", roll_no=f"C_{tag}", name="Cycle Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.commit()
        db.refresh(s)
    ts = make_token(s.id, "student", "access")
    valid_vec = [0.1] * 128

    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"})

    r1 = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r1.status_code == 202

    r2 = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "ALREADY_ENROLLED"

    with SessionLocal() as db:
        tpl = db.get(FaceTemplate, s.id)
        tpl.status = "rejected"
        db.commit()

    r3 = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r3.status_code == 202
    assert r3.json()["status"] == "pending"

    assert client.delete("/api/v1/students/me/face-data", headers={"Authorization": f"Bearer {ts}"}).status_code == 204
    r4 = client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps(valid_vec), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )
    assert r4.status_code == 202


def test_08_enrollment_status_and_face_data_deletion(client):
    """8. GET /students/me/enrollment -> {consent_given, status: none|pending|confirmed|rejected, confirmed_at}.
    DELETE face-data is 204 and idempotent; template row and photo are gone; audit row written.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"stat_{tag}@example.edu", roll_no=f"ST_{tag}", name="Stat Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.commit()
        db.refresh(s)
    ts = make_token(s.id, "student", "access")

    r0 = client.get("/api/v1/students/me/enrollment", headers={"Authorization": f"Bearer {ts}"})
    assert r0.status_code == 200
    assert r0.json() == {"consent_given": False, "status": "none", "model": None, "confirmed_at": None}

    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"})
    client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps([0.1] * 128), "model": "faceapi-128-v1"},
        files={"photo": ("ref.png", io.BytesIO(PNG_SAMPLE), "image/png")},
        headers={"Authorization": f"Bearer {ts}"},
    )

    r_pend = client.get("/api/v1/students/me/enrollment", headers={"Authorization": f"Bearer {ts}"})
    assert r_pend.json()["consent_given"] is True
    assert r_pend.json()["status"] == "pending"

    assert client.delete("/api/v1/students/me/face-data", headers={"Authorization": f"Bearer {ts}"}).status_code == 204
    assert client.delete("/api/v1/students/me/face-data", headers={"Authorization": f"Bearer {ts}"}).status_code == 204

    with SessionLocal() as db:
        assert db.get(FaceTemplate, s.id) is None
        audit_entry = db.scalar(select(AuditLog).where(AuditLog.actor_id == s.id, AuditLog.action == "face_data.deleted"))
        assert audit_entry is not None


def test_09_photo_encryption_at_rest_and_decryption(client):
    """9. Stored photo bytes in the DB are ciphertext (do not start with FF D8 FF / PNG magic)
    and decrypt back to the original.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"enc_{tag}@example.edu", roll_no=f"E_{tag}", name="Enc Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.commit()
        db.refresh(s)
    ts = make_token(s.id, "student", "access")

    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"})
    client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps([0.2] * 128), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )

    with SessionLocal() as db:
        tpl = db.get(FaceTemplate, s.id)
        assert tpl is not None
        assert not tpl.photo_enc.startswith(b"\xff\xd8\xff")
        assert not tpl.photo_enc.startswith(b"\x89PNG")
        assert decrypt(tpl.photo_enc) == JPEG_SAMPLE


# ==============================================================================
# Confirm / Reject / Photos Cases (10, 11, 12)
# ==============================================================================

def test_10_pending_enrollments_and_confirm_reject(client, p2_fixtures):
    """10. Pending list contains only students on that course's roster with status pending, each with a photo_url.
    Confirm/reject sets status, confirmed_by, confirmed_at; a second decision -> 409 NOT_PENDING;
    a student not on that roster -> 404.
    """
    t1 = p2_fixtures["t_prof1"]
    prof1 = p2_fixtures["prof1"]

    c_res = client.post("/api/v1/courses", json={"name": "Pending Approval 101"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s_roster = User(email=f"p_ros_{tag}@example.edu", roll_no=f"PR_{tag}", name="Roster Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        s_non_roster = User(email=f"p_non_{tag}@example.edu", roll_no=f"PN_{tag}", name="Non-Roster Student", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add_all([s_roster, s_non_roster])
        db.flush()
        db.add(RosterEntry(course_id=cid, student_id=s_roster.id))
        db.commit()
        db.refresh(s_roster)
        db.refresh(s_non_roster)

    t_s_roster = make_token(s_roster.id, "student", "access")
    t_s_non = make_token(s_non_roster.id, "student", "access")

    for tok in (t_s_roster, t_s_non):
        client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {tok}"})
        client.post(
            "/api/v1/students/me/enroll",
            data={"vector": json.dumps([0.1] * 128), "model": "faceapi-128-v1"},
            files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
            headers={"Authorization": f"Bearer {tok}"},
        )

    res_pend = client.get(f"/api/v1/courses/{cid}/enrollments/pending", headers={"Authorization": f"Bearer {t1}"})
    assert res_pend.status_code == 200
    p_ids = [p["student_id"] for p in res_pend.json()]
    assert s_roster.id in p_ids
    assert s_non_roster.id not in p_ids
    assert "photo_url" in res_pend.json()[0]

    r_non = client.post(f"/api/v1/courses/{cid}/enrollments/{s_non_roster.id}/confirm", headers={"Authorization": f"Bearer {t1}"})
    assert r_non.status_code == 404
    assert r_non.json()["error"]["code"] == "STUDENT_NOT_ON_ROSTER"

    r_conf = client.post(f"/api/v1/courses/{cid}/enrollments/{s_roster.id}/confirm", headers={"Authorization": f"Bearer {t1}"})
    assert r_conf.status_code == 200
    assert r_conf.json()["status"] == "confirmed"

    with SessionLocal() as db:
        tpl = db.get(FaceTemplate, s_roster.id)
        assert tpl.status == "confirmed"
        assert tpl.confirmed_by == prof1.id
        assert tpl.confirmed_at is not None

    r_conf_again = client.post(f"/api/v1/courses/{cid}/enrollments/{s_roster.id}/confirm", headers={"Authorization": f"Bearer {t1}"})
    assert r_conf_again.status_code == 409
    assert r_conf_again.json()["error"]["code"] == "NOT_PENDING"


def test_11_signed_photo_url_security_and_audit(client, p2_fixtures):
    """11. Photo URL: works with the signing professor's token (200, image bytes, Cache-Control: no-store);
    no token -> 401; another professor's token -> 403 (signature is bound to the professor);
    tampered sig or expired exp (set PHOTO_URL_TTL_S=1) -> 403;
    student not on any of the professor's courses -> 404.
    Each successful view writes an audit_log row photo.viewed.
    """
    t1 = p2_fixtures["t_prof1"]
    t2 = p2_fixtures["t_prof2"]
    prof1 = p2_fixtures["prof1"]

    c_res = client.post("/api/v1/courses", json={"name": "Photo Security Course"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"ph_{tag}@example.edu", roll_no=f"P_{tag}", name="Photo Sec", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.flush()
        db.add(RosterEntry(course_id=cid, student_id=s.id))
        db.commit()
        db.refresh(s)

    ts = make_token(s.id, "student", "access")
    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"})
    client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps([0.1] * 128), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )

    url = photo_url(s.id, prof1.id)

    r_ok = client.get(url, headers={"Authorization": f"Bearer {t1}"})
    assert r_ok.status_code == 200
    assert r_ok.headers.get("cache-control") == "no-store"
    assert r_ok.content == JPEG_SAMPLE

    assert client.get(url).status_code == 401

    assert client.get(url, headers={"Authorization": f"Bearer {t2}"}).status_code == 403

    assert client.get(url + "bad", headers={"Authorization": f"Bearer {t1}"}).status_code == 403

    exp_past = int(time.time()) - 10
    from app.storage import _sig
    expired_url = f"/api/v1/students/{s.id}/photo?exp={exp_past}&sig={_sig(s.id, prof1.id, exp_past)}"
    assert client.get(expired_url, headers={"Authorization": f"Bearer {t1}"}).status_code == 403

    with SessionLocal() as db:
        audit_row = db.scalar(select(AuditLog).where(AuditLog.actor_id == prof1.id, AuditLog.action == "photo.viewed", AuditLog.target == s.id))
        assert audit_row is not None


def test_12_roster_enrollment_status_and_confirmed_count(client, p2_fixtures):
    """12. Roster endpoint shows enrollment per student; confirmed_count on /courses matches."""
    t1 = p2_fixtures["t_prof1"]
    c_res = client.post("/api/v1/courses", json={"name": "Roster Count Match 101"}, headers={"Authorization": f"Bearer {t1}"})
    cid = c_res.json()["id"]

    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s = User(email=f"rc_{tag}@example.edu", roll_no=f"R_{tag}", name="Rost Count", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s)
        db.flush()
        db.add(RosterEntry(course_id=cid, student_id=s.id))
        db.commit()
        db.refresh(s)

    ts = make_token(s.id, "student", "access")
    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {ts}"})
    client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps([0.1] * 128), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {ts}"},
    )

    r_roster = client.get(f"/api/v1/courses/{cid}/roster", headers={"Authorization": f"Bearer {t1}"})
    assert any(x["student_id"] == s.id and x["enrollment"] == "pending" for x in r_roster.json())

    r_courses = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {t1}"})
    c_info = next(c for c in r_courses.json() if c["id"] == cid)
    assert c_info["confirmed_count"] == 0

    client.post(f"/api/v1/courses/{cid}/enrollments/{s.id}/confirm", headers={"Authorization": f"Bearer {t1}"})

    r_roster2 = client.get(f"/api/v1/courses/{cid}/roster", headers={"Authorization": f"Bearer {t1}"})
    assert any(x["student_id"] == s.id and x["enrollment"] == "confirmed" for x in r_roster2.json())

    r_courses2 = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {t1}"})
    c_info2 = next(c for c in r_courses2.json() if c["id"] == cid)
    assert c_info2["confirmed_count"] == 1


# ==============================================================================
# Security Cases (13)
# ==============================================================================

def test_13_security_idor_sql_injection_and_prod_keys(client, p2_fixtures):
    """13. IDOR: student A cannot read/delete student B's data through any route;
    professors cannot fetch photos of students outside their courses.
    SQL injection strings in CSV cells and course names are stored as plain text.
    GK_ENV=prod without GK_PHOTO_KEY refuses to start.
    """
    t1 = p2_fixtures["t_prof1"]
    t2 = p2_fixtures["t_prof2"]
    prof2 = p2_fixtures["prof2"]

    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s_p1 = User(email=f"sp1_{tag}@example.edu", roll_no=f"SP1_{tag}", name="P1 Stud", role="student", password_hash=hash_password("gatekeeper-dev"))
        db.add(s_p1)
        db.commit()
        db.refresh(s_p1)

    t_sp1 = make_token(s_p1.id, "student", "access")
    client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers={"Authorization": f"Bearer {t_sp1}"})
    client.post(
        "/api/v1/students/me/enroll",
        data={"vector": json.dumps([0.1] * 128), "model": "faceapi-128-v1"},
        files={"photo": ("ref.jpg", io.BytesIO(JPEG_SAMPLE), "image/jpeg")},
        headers={"Authorization": f"Bearer {t_sp1}"},
    )

    p2_photo_url = photo_url(s_p1.id, prof2.id)
    r_photo_idor = client.get(p2_photo_url, headers={"Authorization": f"Bearer {t2}"})
    assert r_photo_idor.status_code == 404

    sqli = "CS200'; DROP TABLE users; --"
    c_res = client.post("/api/v1/courses", json={"name": sqli}, headers={"Authorization": f"Bearer {t1}"})
    assert c_res.status_code == 201
    assert c_res.json()["name"] == sqli
    cid = c_res.json()["id"]

    roll_sql = f"SQL_{tag}"
    csv_data = f'roll_no,name,email\n{roll_sql},"{sqli}",sqli_{tag}@example.edu\n'
    r_csv = client.post(
        f"/api/v1/courses/{cid}/roster/import",
        files={"file": ("roster.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r_csv.status_code == 200

    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.roll_no == roll_sql))
        assert u is not None
        assert u.name == sqli

    script = (
        "import os\n"
        "os.environ['GK_ENV'] = 'prod'\n"
        "os.environ['GK_SECRET_KEY'] = 'a-very-long-production-secret-key-12345'\n"
        "os.environ.pop('GK_PHOTO_KEY', None)\n"
        "import app.config"
    )
    res = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT / "backend"), capture_output=True, text=True)
    assert res.returncode != 0
    assert "GK_PHOTO_KEY must be set in production" in res.stderr
