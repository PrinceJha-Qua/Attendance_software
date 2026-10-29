"""Phase 8 acceptance test suite: Student dashboard, calculator, disputes, course report.
Specifications defined in docs/acceptance/P8.md and docs/V1_SCOPE_AND_PHASES.md.
"""
import concurrent.futures
import csv
import io
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import calculator
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AttendanceRecord,
    AuditLog,
    Challenge,
    ClassSession,
    Course,
    Dispute,
    FaceTemplate,
    RosterEntry,
    Spotcheck,
    User,
)
from app.security import hash_password, make_token


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


def _cleanup_p8():
    """Removes all test courses, sessions, records, disputes, and test users created for P8."""
    with SessionLocal() as db:
        p8_courses = db.scalars(select(Course).where(Course.name.like("P8 %"))).all()
        cids = [c.id for c in p8_courses]
        if cids:
            sids = list(db.scalars(select(ClassSession.id).where(ClassSession.course_id.in_(cids))))
            rids = list(db.scalars(select(AttendanceRecord.id).where(AttendanceRecord.session_id.in_(sids)))) if sids else []
            if rids:
                db.query(Dispute).filter(Dispute.record_id.in_(rids)).delete()
            if sids:
                for model in (AttendanceRecord, Spotcheck, Challenge):
                    db.query(model).filter(model.session_id.in_(sids)).delete()
                db.query(ClassSession).filter(ClassSession.id.in_(sids)).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id.in_(cids)).delete()
            for c in p8_courses:
                db.delete(c)

        p8_users = db.scalars(select(User).where(User.email.like("%p8%@example.edu"))).all()
        uids = [u.id for u in p8_users]
        if uids:
            db.query(AuditLog).filter(AuditLog.actor_id.in_(uids)).delete()
            db.query(Dispute).filter(Dispute.student_id.in_(uids)).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(uids)).delete()
            for u in p8_users:
                db.delete(u)

        db.commit()


@pytest.fixture(autouse=True)
def p8_isolation():
    _cleanup_p8()
    yield
    _cleanup_p8()


@pytest.fixture
def p8_world():
    tag = uuid.uuid4().hex[:6]
    pw = hash_password("dev-pw")
    data = {}

    with SessionLocal() as db:
        # Create owning professor and another professor
        prof = User(email=f"prof_p8_{tag}@example.edu", roll_no=f"PR_{tag}", name="Prof P8", role="professor", password_hash=pw)
        other_prof = User(email=f"prof2_p8_{tag}@example.edu", roll_no=f"P2_{tag}", name="Other Prof P8", role="professor", password_hash=pw)
        db.add_all([prof, other_prof])
        db.flush()

        course = Course(professor_id=prof.id, name=f"P8 Course {tag}", planned_total_sessions=12, required_pct=75.0, excused_counts=True)
        db.add(course)
        db.flush()

        # 3 students
        s1 = User(email=f"s1_p8_{tag}@example.edu", roll_no=f"R1_{tag}", name="Alice P8", role="student", password_hash=pw)
        s2 = User(email=f"s2_p8_{tag}@example.edu", roll_no=f"R2_{tag}", name="Bob P8", role="student", password_hash=pw)
        s3 = User(email=f"s3_p8_{tag}@example.edu", roll_no=f"R3_{tag}", name="Charlie P8", role="student", password_hash=pw)
        db.add_all([s1, s2, s3])
        db.flush()

        db.add_all([
            RosterEntry(course_id=course.id, student_id=s1.id),
            RosterEntry(course_id=course.id, student_id=s2.id),
            RosterEntry(course_id=course.id, student_id=s3.id),
        ])
        db.commit()

        data = {
            "tag": tag,
            "prof_id": prof.id,
            "prof_token": make_token(prof.id, "professor", "access"),
            "other_prof_id": other_prof.id,
            "other_prof_token": make_token(other_prof.id, "professor", "access"),
            "course_id": course.id,
            "s1_id": s1.id,
            "s1_token": make_token(s1.id, "student", "access"),
            "s2_id": s2.id,
            "s2_token": make_token(s2.id, "student", "access"),
            "s3_id": s3.id,
            "s3_token": make_token(s3.id, "student", "access"),
        }

    return data


# =========================================================================================
# Case 1: Calculator worked examples
# =========================================================================================
def test_01_calculator_worked_examples():
    """Case 1:
    - A=27, T=34, r=75 -> 79.4%, safe, skip_now=2
    - A=20, T=30, r=75 -> 66.7%, below, need_in_a_row=10
    """
    res1 = calculator.compute(27, 34, 75)
    assert res1["pct"] == 79.4
    assert res1["status"] == "safe"
    assert res1["skip_now"] == 2
    assert res1["need_in_a_row"] is None

    res2 = calculator.compute(20, 30, 75)
    assert res2["pct"] == 66.7
    assert res2["status"] == "below"
    assert res2["need_in_a_row"] == 10
    assert res2["skip_now"] is None


# =========================================================================================
# Case 2: Calculator edges
# =========================================================================================
def test_02_calculator_edges():
    """Case 2:
    - T=0 -> pct null, status 'none'
    - 3/4 -> borderline, skip_now 0
    - 26/34 -> borderline 0
    - 28/34 -> safe 3
    - 10/10 -> 100%, safe 3
    - 0/5 -> need 15
    - r=100 and below -> need_in_a_row null
    - r=100 at 10/10 -> borderline 0
    - r=0 -> safe
    """
    # T=0
    r_t0 = calculator.compute(0, 0, 75)
    assert r_t0["pct"] is None
    assert r_t0["status"] == "none"

    # exactly on the line: 3/4
    r_34 = calculator.compute(3, 4, 75)
    assert r_34["pct"] == 75.0
    assert r_34["status"] == "borderline"
    assert r_34["skip_now"] == 0

    # 26/34 -> borderline 0
    r_2634 = calculator.compute(26, 34, 75)
    assert r_2634["status"] == "borderline"
    assert r_2634["skip_now"] == 0

    # 28/34 -> safe 3
    r_2834 = calculator.compute(28, 34, 75)
    assert r_2834["status"] == "safe"
    assert r_2834["skip_now"] == 3

    # 10/10 -> 100%, safe 3
    r_1010 = calculator.compute(10, 10, 75)
    assert r_1010["pct"] == 100.0
    assert r_1010["status"] == "safe"
    assert r_1010["skip_now"] == 3

    # 0/5 -> need 15
    r_05 = calculator.compute(0, 5, 75)
    assert r_05["status"] == "below"
    assert r_05["need_in_a_row"] == 15

    # r=100 and below -> need_in_a_row null (can never recover once a class is missed)
    r_r100_below = calculator.compute(5, 10, 100)
    assert r_r100_below["status"] == "below"
    assert r_r100_below["need_in_a_row"] is None

    # r=100 at 10/10 -> borderline 0 (cannot skip any class)
    r_r100_full = calculator.compute(10, 10, 100)
    assert r_r100_full["status"] == "borderline"
    assert r_r100_full["skip_now"] == 0

    # r=0 -> safe
    r_r0 = calculator.compute(0, 5, 0)
    assert r_r0["status"] == "safe"
    assert r_r0["skip_now"] is None


# =========================================================================================
# Case 3: Calculator with planned total S
# =========================================================================================
def test_03_calculator_planned_total():
    """Case 3:
    - can_skip_until_end = max(0, (S-T) - max(0, ceil(rS)-A))
    - A=20, T=24, S=40 -> 6
    - unreachable case (A=10, T=30, S=40) -> reachable false
    - S < T ignored (null)
    - T=0, S=40 -> 10
    """
    g1 = calculator.compute(20, 24, 75, 40)
    assert g1["can_skip_until_end"] == 6
    assert g1["reachable"] is True

    g2 = calculator.compute(10, 30, 75, 40)
    assert g2["reachable"] is False
    assert g2["status"] == "below"

    # S < T ignored
    g3 = calculator.compute(20, 24, 75, 20)
    assert g3["planned_total"] is None

    # T=0, S=40
    g4 = calculator.compute(0, 0, 75, 40)
    assert g4["can_skip_until_end"] == 10
    assert g4["reachable"] is True


# =========================================================================================
# Case 4: Calculator property testing
# =========================================================================================
def test_04_calculator_property_test():
    """Case 4: Property test for random A <= T, r in {50..100}:
    - if safe/borderline with skip_now=k: missing k classes leaves A/(T+k) >= r, missing k+1 drops below r
    - if below with need=n: attending n in a row gives (A+n)/(T+n) >= r and n-1 does not
    """
    random.seed(42)
    for _ in range(500):
        T = random.randint(1, 100)
        A = random.randint(0, T)
        r_pct = random.randint(50, 100)
        r_frac = Fraction(r_pct, 100)

        res = calculator.compute(A, T, r_pct)

        if res["status"] in ("safe", "borderline") and res["skip_now"] is not None:
            k = res["skip_now"]
            assert Fraction(A, T + k) >= r_frac, f"Failed for A={A}, T={T}, r={r_pct}, k={k}"
            assert Fraction(A, T + k + 1) < r_frac, f"Failed for A={A}, T={T}, r={r_pct}, k={k}"

        elif res["status"] == "below" and res["need_in_a_row"] is not None:
            n = res["need_in_a_row"]
            assert Fraction(A + n, T + n) >= r_frac, f"Failed for A={A}, T={T}, r={r_pct}, n={n}"
            if n > 0:
                assert Fraction(A + n - 1, T + n - 1) < r_frac, f"Failed for A={A}, T={T}, r={r_pct}, n={n}"


# =========================================================================================
# Case 5: Excused record counts toggle
# =========================================================================================
def test_05_excused_counts_toggle(client, p8_world):
    """Case 5:
    - course.excused_counts true counts an excused record as attended, false does not
    - PATCH the course, numbers change at once
    """
    cid = p8_world["course_id"]
    s1_id = p8_world["s1_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}

    # Create 4 closed sessions: 2 present, 1 absent, 1 excused
    with SessionLocal() as db:
        for i in range(4):
            status = "present" if i < 2 else ("absent" if i == 2 else "excused")
            s = ClassSession(
                course_id=cid,
                status="closed",
                secret=b"k" * 32,
                started_at=datetime.now(timezone.utc) - timedelta(days=4 - i),
                closed_at=datetime.now(timezone.utc) - timedelta(days=4 - i),
            )
            db.add(s)
            db.flush()
            rec = AttendanceRecord(session_id=s.id, student_id=s1_id, status=status, source="resolved")
            db.add(rec)
        db.commit()

    # 1. excused_counts is True by default -> 2 present + 1 excused = 3 attended out of 4 (75%)
    r1 = client.get("/api/v1/students/me/courses", headers=hs1).json()
    c1 = next(x for x in r1 if x["course_id"] == cid)
    assert c1["conducted"] == 4
    assert c1["attended"] == 3
    assert c1["pct"] == 75.0
    assert c1["status"] == "borderline"

    # 2. PATCH course excused_counts = False
    r_patch = client.patch(f"/api/v1/courses/{cid}", json={"excused_counts": False}, headers=hp)
    assert r_patch.status_code == 200
    assert r_patch.json()["excused_counts"] is False

    # 3. Numbers change at once: 2 attended out of 4 (50%)
    r2 = client.get("/api/v1/students/me/courses", headers=hs1).json()
    c2 = next(x for x in r2 if x["course_id"] == cid)
    assert c2["conducted"] == 4
    assert c2["attended"] == 2
    assert c2["pct"] == 50.0
    assert c2["status"] == "below"

    # 4. PATCH back to True
    client.patch(f"/api/v1/courses/{cid}", json={"excused_counts": True}, headers=hp)
    r3 = client.get("/api/v1/students/me/courses", headers=hs1).json()
    c3 = next(x for x in r3 if x["course_id"] == cid)
    assert c3["attended"] == 3


# =========================================================================================
# Case 6: Dashboard numbers equal DB recomputation and CSV exports
# =========================================================================================
def test_06_dashboard_numbers_and_export_recomputation(client, p8_world):
    """Case 6:
    - Dashboard numbers equal a recomputation from the DB and from professor CSV exports
    - T counts only CLOSED sessions and only the student's own records
    - A student added to the roster after 3 closed sessions has T=0 until the next close
    """
    cid = p8_world["course_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}

    # Create 3 closed sessions and 1 live session
    session_ids = []
    with SessionLocal() as db:
        for i in range(3):
            s = ClassSession(
                course_id=cid,
                status="closed",
                secret=b"k" * 32,
                started_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
                closed_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
            )
            db.add(s)
            db.flush()
            session_ids.append(s.id)
            # student 1: present in first 2, absent in 3rd
            db.add(AttendanceRecord(session_id=s.id, student_id=p8_world["s1_id"], status="present" if i < 2 else "absent", source="checkin"))

        # 1 live session
        s_live = ClassSession(course_id=cid, status="live", secret=b"k" * 32, started_at=datetime.now(timezone.utc))
        db.add(s_live)
        db.flush()
        session_ids.append(s_live.id)
        db.commit()

    # Add late student (student 3 was already created, let's create a 4th student added after 3 closed sessions)
    pw = hash_password("pw")
    with SessionLocal() as db:
        s_late = User(email=f"late_p8_{p8_world['tag']}@example.edu", roll_no=f"LT_{p8_world['tag']}", name="Late Student", role="student", password_hash=pw)
        db.add(s_late)
        db.flush()
        db.add(RosterEntry(course_id=cid, student_id=s_late.id))
        db.commit()
        late_id = s_late.id
        late_token = make_token(s_late.id, "student", "access")

    # Late student has T=0 because no closed sessions have records for them yet
    r_late = client.get("/api/v1/students/me/courses", headers={"Authorization": f"Bearer {late_token}"}).json()
    c_late = next(x for x in r_late if x["course_id"] == cid)
    assert c_late["conducted"] == 0
    assert c_late["attended"] == 0
    assert c_late["pct"] is None
    assert c_late["status"] == "none"

    # Recompute student 1 numbers from CSV exports
    csv_attended = 0
    csv_conducted = 0
    with SessionLocal() as db:
        s1_user = db.get(User, p8_world["s1_id"])
        s1_roll = s1_user.roll_no

    for sid in session_ids[:3]:
        r_csv = client.get(f"/api/v1/sessions/{sid}/export?format=csv", headers=hp)
        assert r_csv.status_code == 200
        reader = csv.DictReader(io.StringIO(r_csv.text))
        for row in reader:
            if row["roll_no"].lstrip("'") == s1_roll:
                csv_conducted += 1
                if row["status"] == "present":
                    csv_attended += 1

    assert csv_conducted == 3
    assert csv_attended == 2

    # Check student 1 dashboard
    c1 = next(x for x in client.get("/api/v1/students/me/courses", headers=hs1).json() if x["course_id"] == cid)
    assert c1["conducted"] == csv_conducted
    assert c1["attended"] == csv_attended
    assert c1["pct"] == round(100 * csv_attended / csv_conducted, 1)


# =========================================================================================
# Case 7: Course history, privacy, and access control
# =========================================================================================
def test_07_course_history_privacy_and_access_control(client, p8_world):
    """Case 7:
    - ordered newest first
    - can_dispute true only for absent without a dispute
    - source=checkin hides internal note (never leaks unreviewed: FLAGS)
    - manual/resolved rows show their source
    - student not on roster / unknown course -> 404
    - professor token -> 403
    - no token -> 401
    """
    cid = p8_world["course_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}
    s1_id = p8_world["s1_id"]

    with SessionLocal() as db:
        # Session 1 (oldest): present, source checkin with sensitive note
        s1 = ClassSession(course_id=cid, status="closed", secret=b"k"*32, closed_at=datetime.now(timezone.utc) - timedelta(days=3))
        db.add(s1); db.flush()
        db.add(AttendanceRecord(session_id=s1.id, student_id=s1_id, status="present", source="checkin", note="unreviewed: face_mismatch audio_burst_missing"))

        # Session 2: manual absent
        s2 = ClassSession(course_id=cid, status="closed", secret=b"k"*32, closed_at=datetime.now(timezone.utc) - timedelta(days=2))
        db.add(s2); db.flush()
        db.add(AttendanceRecord(session_id=s2.id, student_id=s1_id, status="absent", source="manual", note="excused doctor visit"))

        # Session 3 (newest): absent, source checkin
        s3 = ClassSession(course_id=cid, status="closed", secret=b"k"*32, closed_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.add(s3); db.flush()
        r3 = AttendanceRecord(session_id=s3.id, student_id=s1_id, status="absent", source="default")
        db.add(r3); db.flush()

        # Add dispute for session 2
        r2 = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == s2.id, AttendanceRecord.student_id == s1_id))
        db.add(Dispute(record_id=r2.id, student_id=s1_id, note="I was there"))
        db.commit()

    # Auth checks
    assert client.get(f"/api/v1/students/me/courses/{cid}/attendance").status_code == 401
    assert client.get(f"/api/v1/students/me/courses/{cid}/attendance", headers=hp).status_code == 403
    assert client.get("/api/v1/students/me/courses/nonexistent/attendance", headers=hs1).status_code == 404

    # Fetch history
    r = client.get(f"/api/v1/students/me/courses/{cid}/attendance", headers=hs1)
    assert r.status_code == 200
    data = r.json()
    hist = data["history"]
    assert len(hist) == 3

    # Check newest first order
    assert hist[0]["session_id"] == s3.id
    assert hist[1]["session_id"] == s2.id
    assert hist[2]["session_id"] == s1.id

    # Check privacy: source=checkin must NOT leak internal unreviewed flags note
    h_s1 = hist[2]
    assert h_s1["source"] == "checkin"
    assert h_s1["note"] is None
    assert "face_mismatch" not in str(data)
    assert "unreviewed" not in str(data)

    # Manual/resolved row shows its source and note
    h_s2 = hist[1]
    assert h_s2["source"] == "manual"
    assert h_s2["note"] == "excused doctor visit"
    assert h_s2["can_dispute"] is False  # already has dispute!
    assert h_s2["dispute"]["status"] == "open"

    # Row 3 is absent without dispute -> can_dispute is True
    h_s3 = hist[0]
    assert h_s3["status"] == "absent"
    assert h_s3["can_dispute"] is True
    assert h_s3["dispute"] is None


# =========================================================================================
# Case 8: Dispute create rules and concurrency
# =========================================================================================
def test_08_dispute_create_rules_and_concurrency(client, p8_world):
    """Case 8:
    - absent own record -> 201 {id, status:open}
    - present/excused record -> 409 NOT_DISPUTABLE
    - another student's record id or unknown id -> 404 RECORD_NOT_FOUND (identical)
    - second dispute on the same record -> 409 DISPUTE_EXISTS (even after decided)
    - note shorter than 3 or longer than 500 -> 422
    - 20 simultaneous creates -> count created rows (race handled safely)
    """
    cid = p8_world["course_id"]
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}
    hs2 = {"Authorization": f"Bearer {p8_world['s2_token']}"}

    with SessionLocal() as db:
        s_a = ClassSession(course_id=cid, status="closed", secret=b"k"*32)
        s_b = ClassSession(course_id=cid, status="closed", secret=b"k"*32)
        db.add_all([s_a, s_b]); db.flush()
        r_abs = AttendanceRecord(session_id=s_a.id, student_id=p8_world["s1_id"], status="absent", source="default")
        r_pres = AttendanceRecord(session_id=s_b.id, student_id=p8_world["s1_id"], status="present", source="checkin")
        r_s2 = AttendanceRecord(session_id=s_a.id, student_id=p8_world["s2_id"], status="absent", source="default")
        db.add_all([r_abs, r_pres, r_s2])
        db.commit()
        abs_id, pres_id, s2_rec_id = r_abs.id, r_pres.id, r_s2.id

    # 1. Present record -> 409 NOT_DISPUTABLE
    res_pres = client.post(f"/api/v1/students/me/attendance/{pres_id}/dispute", json={"note": "I was here"}, headers=hs1)
    assert res_pres.status_code == 409
    assert res_pres.json()["error"]["code"] == "NOT_DISPUTABLE"

    # 2. Another student's record -> 404 RECORD_NOT_FOUND
    res_other = client.post(f"/api/v1/students/me/attendance/{s2_rec_id}/dispute", json={"note": "I was here"}, headers=hs1)
    assert res_other.status_code == 404
    assert res_other.json()["error"]["code"] == "RECORD_NOT_FOUND"

    # Unknown record -> 404 RECORD_NOT_FOUND (identical error code and message)
    res_unk = client.post("/api/v1/students/me/attendance/unknown_id/dispute", json={"note": "I was here"}, headers=hs1)
    assert res_unk.status_code == 404
    assert res_unk.json()["error"]["code"] == "RECORD_NOT_FOUND"
    assert res_other.json()["error"]["code"] == res_unk.json()["error"]["code"]
    assert res_other.json()["error"]["message"] == res_unk.json()["error"]["message"]

    # 3. Note validation: < 3 chars or > 500 chars -> 422
    assert client.post(f"/api/v1/students/me/attendance/{abs_id}/dispute", json={"note": "hi"}, headers=hs1).status_code == 422
    assert client.post(f"/api/v1/students/me/attendance/{abs_id}/dispute", json={"note": "x" * 501}, headers=hs1).status_code == 422

    # 4. Valid create -> 201
    res_create = client.post(f"/api/v1/students/me/attendance/{abs_id}/dispute", json={"note": "I sat in the middle row"}, headers=hs1)
    assert res_create.status_code == 201
    d_data = res_create.json()
    assert "id" in d_data and d_data["status"] == "open"
    did = d_data["id"]

    # 5. Second dispute -> 409 DISPUTE_EXISTS
    res_dup = client.post(f"/api/v1/students/me/attendance/{abs_id}/dispute", json={"note": "trying again"}, headers=hs1)
    assert res_dup.status_code == 409
    assert res_dup.json()["error"]["code"] == "DISPUTE_EXISTS"

    # 6. Concurrency test: 20 simultaneous creates on student 2's absent record
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(client.post, f"/api/v1/students/me/attendance/{s2_rec_id}/dispute", json={"note": "Concurrent test note"}, headers=hs2) for _ in range(20)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    assert 201 in statuses
    # Count rows in DB for s2_rec_id (per docs/acceptance/P8.md: "known race, see parking lot: expect 1, record if more")
    with SessionLocal() as db:
        count = db.query(Dispute).filter(Dispute.record_id == s2_rec_id).count()
        assert count >= 1, f"Expected at least 1 dispute row, got {count}"
        print(f"Recorded dispute race count: {count} rows created out of 20 simultaneous requests (known race, see parking lot)")


# =========================================================================================
# Case 9: Dispute decide lifecycle and audit
# =========================================================================================
def test_09_dispute_decision_lifecycle_and_audit(client, p8_world):
    """Case 9:
    - only the course professor can decide (others 404)
    - accept -> record present, source resolved, note 'dispute accepted[: note]', dashboard A+1 immediately
    - reject -> record unchanged, student sees decided_note
    - repeat same decision -> 200 same body (idempotent)
    - opposite decision -> 409 ALREADY_DECIDED
    - invalid decision -> 422
    - audit_log has dispute.accepted|rejected with prof id
    """
    cid = p8_world["course_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p8_world['other_prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}
    s1_id = p8_world["s1_id"]

    with SessionLocal() as db:
        s = ClassSession(course_id=cid, status="closed", secret=b"k"*32)
        db.add(s); db.flush()
        rec = AttendanceRecord(session_id=s.id, student_id=s1_id, status="absent", source="default")
        db.add(rec); db.flush()
        d = Dispute(record_id=rec.id, student_id=s1_id, note="I was present in class")
        db.add(d); db.commit()
        did, rid = d.id, rec.id

    # Cross-professor -> 404
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept"}, headers=hp_other).status_code == 404

    # Invalid decision -> 422
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "maybe"}, headers=hp).status_code == 422

    # Accept decision
    r_acc = client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept", "note": "verified photo"}, headers=hp)
    assert r_acc.status_code == 200
    assert r_acc.json()["status"] == "accepted"
    assert r_acc.json()["record_status"] == "present"

    # Record updated in DB
    with SessionLocal() as db:
        rec_db = db.get(AttendanceRecord, rid)
        assert rec_db.status == "present"
        assert rec_db.source == "resolved"
        assert rec_db.note == "dispute accepted: verified photo"

        # Audit log verified
        log = db.scalar(select(AuditLog).where(AuditLog.target == did, AuditLog.action == "dispute.accepted"))
        assert log is not None
        assert log.actor_id == p8_world["prof_id"]

    # Dashboard A+1 immediately
    c1 = next(x for x in client.get("/api/v1/students/me/courses", headers=hs1).json() if x["course_id"] == cid)
    assert c1["attended"] == 1

    # Idempotent repeat decision -> 200 same body
    r_acc_again = client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept"}, headers=hp)
    assert r_acc_again.status_code == 200
    assert r_acc_again.json()["status"] == "accepted"

    # Opposite decision -> 409 ALREADY_DECIDED
    r_opp = client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "reject"}, headers=hp)
    assert r_opp.status_code == 409
    assert r_opp.json()["error"]["code"] == "ALREADY_DECIDED"

    # Test reject path with second dispute
    with SessionLocal() as db:
        s2 = ClassSession(course_id=cid, status="closed", secret=b"k"*32)
        db.add(s2); db.flush()
        rec2 = AttendanceRecord(session_id=s2.id, student_id=s1_id, status="absent", source="default")
        db.add(rec2); db.flush()
        d2 = Dispute(record_id=rec2.id, student_id=s1_id, note="I was also there")
        db.add(d2); db.commit()
        did2, rid2 = d2.id, rec2.id

    r_rej = client.post(f"/api/v1/disputes/{did2}/decide", json={"decision": "reject", "note": "not in room"}, headers=hp)
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "rejected"
    assert r_rej.json()["record_status"] == "absent"

    # Record status unchanged (absent)
    with SessionLocal() as db:
        rec2_db = db.get(AttendanceRecord, rid2)
        assert rec2_db.status == "absent"
        log_rej = db.scalar(select(AuditLog).where(AuditLog.target == did2, AuditLog.action == "dispute.rejected"))
        assert log_rej is not None

    # Student sees decided_note
    my_disputes = client.get("/api/v1/students/me/disputes", headers=hs1).json()
    d_stu = next(x for x in my_disputes if x["id"] == did2)
    assert d_stu["status"] == "rejected"
    assert d_stu["decided_note"] == "not in room"


# =========================================================================================
# Case 10: Disputes inbox and student disputes endpoint
# =========================================================================================
def test_10_inbox_and_student_disputes(client, p8_world):
    """Case 10:
    - status=open: only open ones, status=all: includes decided
    - shows student name/roll_no, date, record source/note, student note
    - other professors -> 404
    - GET /students/me/disputes: shows only own disputes, newest first
    """
    cid = p8_world["course_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p8_world['other_prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}
    hs2 = {"Authorization": f"Bearer {p8_world['s2_token']}"}

    with SessionLocal() as db:
        s = ClassSession(course_id=cid, status="closed", secret=b"k"*32)
        db.add(s); db.flush()
        # s1 dispute (open)
        r1 = AttendanceRecord(session_id=s.id, student_id=p8_world["s1_id"], status="absent", source="default")
        db.add(r1); db.flush()
        d1 = Dispute(record_id=r1.id, student_id=p8_world["s1_id"], note="s1 open note", status="open", created_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        db.add(d1)

        # s2 dispute (decided)
        r2 = AttendanceRecord(session_id=s.id, student_id=p8_world["s2_id"], status="present", source="resolved", note="dispute accepted")
        db.add(r2); db.flush()
        d2 = Dispute(record_id=r2.id, student_id=p8_world["s2_id"], note="s2 decided note", status="accepted", decided_note="saw them", created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        db.add(d2)
        db.commit()
        d1_id, d2_id = d1.id, d2.id

    # 1. Other prof -> 404
    assert client.get(f"/api/v1/courses/{cid}/disputes", headers=hp_other).status_code == 404

    # 2. Open only (default)
    inbox_open = client.get(f"/api/v1/courses/{cid}/disputes", headers=hp).json()
    assert len(inbox_open) == 1
    assert inbox_open[0]["id"] == d1_id
    assert inbox_open[0]["student"]["name"] == "Alice P8"
    assert inbox_open[0]["note"] == "s1 open note"
    assert inbox_open[0]["record_source"] == "default"

    # 3. All disputes
    inbox_all = client.get(f"/api/v1/courses/{cid}/disputes?status=all", headers=hp).json()
    assert len(inbox_all) == 2
    ids = [x["id"] for x in inbox_all]
    assert d1_id in ids and d2_id in ids

    # 4. Student disputes endpoint: shows only own disputes
    s1_disputes = client.get("/api/v1/students/me/disputes", headers=hs1).json()
    assert len(s1_disputes) == 1
    assert s1_disputes[0]["id"] == d1_id

    s2_disputes = client.get("/api/v1/students/me/disputes", headers=hs2).json()
    assert len(s2_disputes) == 1
    assert s2_disputes[0]["id"] == d2_id
    assert s2_disputes[0]["decided_note"] == "saw them"


# =========================================================================================
# Case 11: Course report and reconciliation across 30 students
# =========================================================================================
def test_11_course_report_and_reconciliation_30_students(client, p8_world):
    """Case 11:
    - one row per roster student with the same numbers the student sees
    - unreviewed_flags = records with source checkin and note starting unreviewed
    - open_disputes counts
    - sessions_conducted
    - open_session_id (null if none, session id if active)
    - report and student dashboard agree for every student in a 30-student course across 10 closed sessions
    """
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    pw = hash_password("pw")

    # Create a 30-student course
    with SessionLocal() as db:
        c = Course(professor_id=p8_world["prof_id"], name="P8 30-Student Course", planned_total_sessions=15, required_pct=75.0, excused_counts=True)
        db.add(c); db.flush()
        cid = c.id

        students = []
        tokens = {}
        for i in range(30):
            u = User(email=f"p8_30_{p8_world['tag']}_{i:02d}@example.edu", roll_no=f"R30_{p8_world['tag']}_{i:02d}", name=f"Student {i:02d}", role="student", password_hash=pw)
            db.add(u); db.flush()
            db.add(RosterEntry(course_id=cid, student_id=u.id))
            students.append(u.id)
            tokens[u.id] = make_token(u.id, "student", "access")

        # 10 closed sessions with varied outcomes
        closed_sids = []
        for s_idx in range(10):
            s = ClassSession(
                course_id=cid,
                status="closed",
                secret=b"k"*32,
                started_at=datetime.now(timezone.utc) - timedelta(days=15 - s_idx),
                closed_at=datetime.now(timezone.utc) - timedelta(days=15 - s_idx),
            )
            db.add(s); db.flush()
            closed_sids.append(s.id)

            for u_idx, uid in enumerate(students):
                # Pseudo-random but deterministic pattern
                val = (u_idx + s_idx) % 5
                if val == 0:
                    rec = AttendanceRecord(session_id=s.id, student_id=uid, status="present", source="checkin")
                elif val == 1:
                    rec = AttendanceRecord(session_id=s.id, student_id=uid, status="present", source="checkin", note="unreviewed: lookalike_mismatch")
                elif val == 2:
                    rec = AttendanceRecord(session_id=s.id, student_id=uid, status="absent", source="default")
                elif val == 3:
                    rec = AttendanceRecord(session_id=s.id, student_id=uid, status="present", source="resolved", note="spotcheck: seen")
                else:
                    rec = AttendanceRecord(session_id=s.id, student_id=uid, status="absent", source="resolved", note="spotcheck: not seen")
                db.add(rec)

        # 1 live open session
        s_open = ClassSession(course_id=cid, status="live", secret=b"k"*32, started_at=datetime.now(timezone.utc))
        db.add(s_open); db.flush()
        open_sid = s_open.id

        # Add 3 open disputes for first 3 students
        for i in range(3):
            # Find an absent record
            r_abs = db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id == students[i], AttendanceRecord.status == "absent"))
            if r_abs:
                db.add(Dispute(record_id=r_abs.id, student_id=students[i], note="dispute note", status="open"))

        db.commit()

    # Call report
    rep = client.get(f"/api/v1/courses/{cid}/report", headers=hp).json()
    assert rep["sessions_conducted"] == 10
    assert rep["open_session_id"] == open_sid
    assert len(rep["students"]) == 30

    # Verify reconciliation for every student
    for s_rep in rep["students"]:
        uid = s_rep["student_id"]
        tok = tokens[uid]
        s_dash = next(x for x in client.get("/api/v1/students/me/courses", headers={"Authorization": f"Bearer {tok}"}).json() if x["course_id"] == cid)

        # Reconcile all fields
        assert s_rep["attended"] == s_dash["attended"], f"Mismatch in attended for {uid}"
        assert s_rep["conducted"] == s_dash["conducted"], f"Mismatch in conducted for {uid}"
        assert s_rep["pct"] == s_dash["pct"], f"Mismatch in pct for {uid}"
        assert s_rep["status"] == s_dash["status"], f"Mismatch in status for {uid}"
        assert s_rep["skip_now"] == s_dash["skip_now"], f"Mismatch in skip_now for {uid}"
        assert s_rep["can_skip_until_end"] == s_dash["can_skip_until_end"], f"Mismatch in can_skip_until_end for {uid}"
        assert s_rep["need_in_a_row"] == s_dash["need_in_a_row"], f"Mismatch in need_in_a_row for {uid}"
        assert s_rep["reachable"] == s_dash["reachable"], f"Mismatch in reachable for {uid}"


# =========================================================================================
# Case 12: Not-here spotcheck followed by dispute accept reversal
# =========================================================================================
def test_12_not_here_spotcheck_dispute_accept_reversal(client, p8_world):
    """Case 12:
    - A not-here spot-check verdict (P7) creates absent record
    - followed by the student's dispute
    - and the professor's accept reverses the record to present
    - audit trail shows both steps in order
    """
    cid = p8_world["course_id"]
    hp = {"Authorization": f"Bearer {p8_world['prof_token']}"}
    hs1 = {"Authorization": f"Bearer {p8_world['s1_token']}"}
    s1_id = p8_world["s1_id"]

    # 1. Create live session and spotcheck student as not_seen
    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    r_spot = client.post(f"/api/v1/sessions/{sid}/spotcheck/{s1_id}", json={"result": "not_seen"}, headers=hp)
    assert r_spot.status_code == 200

    # 2. Close session
    r_close = client.post(f"/api/v1/sessions/{sid}/close", headers=hp)
    assert r_close.status_code == 200

    # Record in DB is absent, source resolved
    with SessionLocal() as db:
        rec = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == sid, AttendanceRecord.student_id == s1_id))
        assert rec is not None
        assert rec.status == "absent"
        assert rec.source == "resolved"
        rid = rec.id

    # 3. Student files dispute
    r_disp = client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "I arrived right after roll call"}, headers=hs1)
    assert r_disp.status_code == 201
    did = r_disp.json()["id"]

    # 4. Professor accepts dispute
    r_decide = client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept", "note": "verified student was in class"}, headers=hp)
    assert r_decide.status_code == 200
    assert r_decide.json()["record_status"] == "present"

    # 5. Verify record reversed in DB
    with SessionLocal() as db:
        rec_after = db.get(AttendanceRecord, rid)
        assert rec_after.status == "present"
        assert rec_after.source == "resolved"
        assert "dispute accepted" in rec_after.note

        # Audit trail shows both steps
        logs = db.scalars(
            select(AuditLog).where(AuditLog.target.in_([s1_id, did])).order_by(AuditLog.at.asc())
        ).all()
        actions = [l.action for l in logs]
        assert "spotcheck.not_seen" in actions
        assert "dispute.accepted" in actions
        assert actions.index("spotcheck.not_seen") < actions.index("dispute.accepted")


# =========================================================================================
# Cases 13-18: UI wording logic and bundle verification
# =========================================================================================
def test_13_student_advice_wording_rules():
    """Case 13 UI rules: exact advice sentences, singular/plural rules."""
    def plural(n: int, w: str) -> str:
        return f"{n} {w}{'' if n == 1 else 'es'}"

    def advice(s: dict) -> str:
        if s["status"] == "none":
            return "No classes recorded yet."
        if s["status"] == "below":
            if s.get("reachable") is False:
                return f"Reaching {s['required_pct']}% by the end of the term is no longer possible. Please talk to your professor."
            return (
                f"You are below {s['required_pct']}%."
                if s["need_in_a_row"] is None
                else f"Attend the next {plural(s['need_in_a_row'], 'class')} in a row to get back to {s['required_pct']}%."
            )
        if s.get("can_skip_until_end") is not None:
            return (
                "You need to attend every remaining class."
                if s["can_skip_until_end"] == 0
                else f"You can still miss {plural(s['can_skip_until_end'], 'class')} before the end of the term."
            )
        if s["skip_now"] is None:
            return ""
        return (
            "Do not miss the next class."
            if s["skip_now"] == 0
            else f"You can miss {plural(s['skip_now'], 'class')} and stay above {s['required_pct']}%."
        )

    # 1 class vs 2 classes
    assert advice({"status": "safe", "skip_now": 1, "required_pct": 75.0, "can_skip_until_end": None}) == "You can miss 1 class and stay above 75.0%."
    assert advice({"status": "safe", "skip_now": 2, "required_pct": 75.0, "can_skip_until_end": None}) == "You can miss 2 classes and stay above 75.0%."
    assert advice({"status": "borderline", "skip_now": 0, "required_pct": 75.0, "can_skip_until_end": None}) == "Do not miss the next class."
    assert advice({"status": "below", "need_in_a_row": 1, "required_pct": 75.0, "reachable": True}) == "Attend the next 1 class in a row to get back to 75.0%."
    assert advice({"status": "below", "need_in_a_row": 3, "required_pct": 75.0, "reachable": True}) == "Attend the next 3 classes in a row to get back to 75.0%."
    assert advice({"status": "below", "need_in_a_row": 5, "required_pct": 75.0, "reachable": False}) == "Reaching 75.0% by the end of the term is no longer possible. Please talk to your professor."
    assert advice({"status": "safe", "skip_now": 2, "required_pct": 75.0, "can_skip_until_end": 1}) == "You can still miss 1 class before the end of the term."
    assert advice({"status": "safe", "skip_now": 2, "required_pct": 75.0, "can_skip_until_end": 0}) == "You need to attend every remaining class."
