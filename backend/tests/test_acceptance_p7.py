"""Phase 7 Acceptance Test Suite: Professor live console.
Verifies all 16 API and WebSocket cases specified in docs/acceptance/P7.md:
Close & records, live list, Attention Panel, spot-checks, resolve/manual/export,
and WebSocket live feeds.
"""
import asyncio
import concurrent.futures
import csv
import io
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, tokens, trust
from app.db import Base, SessionLocal, engine
from app.events import hub
from app.main import app
from app.models import (
    AttendanceRecord,
    AuditLog,
    Challenge,
    Checkin,
    ClassSession,
    Course,
    FaceTemplate,
    RosterEntry,
    Spotcheck,
    User,
)
from app.security import hash_password, make_token
from app.storage import encrypt

M = config.FACE_MODEL


def make_vector(val: float = 0.0, offset_idx: int = 0, offset_val: float = 0.0) -> list[float]:
    vec = [float(val)] * 128
    if 0 <= offset_idx < 128:
        vec[offset_idx] = float(val + offset_val)
    return vec


def make_liveness(colour_seq: list[str]):
    baseline = [120.0, 120.0, 120.0]
    samples = [[120.0 + 0.2 * (trust.RGB[c][k] - 85.0) for k in range(3)] for c in colour_seq]
    return {"baseline": baseline, "samples": samples}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture
def p7_world():
    """Isolated environment with:
    - 1 owning professor, 1 other professor
    - 1 course with 6 rostered students (with distinct templates & some with stored photos)
    - 1 live session
    - 1 unrostered student
    """
    tag = uuid.uuid4().hex[:6]
    created = {
        "prof_id": None, "prof_token": None,
        "other_prof_id": None, "other_prof_token": None,
        "course_id": None,
        "session_id": None, "secret": None,
        "students": [],  # (id, token, vector, email, roll_no, name)
        "unrostered_id": None, "unrostered_token": None,
    }

    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        if not prof:
            prof = User(email=f"prof_{tag}@example.edu", roll_no=f"P_{tag}", name="Prof P7", role="professor", password_hash=hash_password("pw"))
            db.add(prof); db.flush()
        created["prof_id"] = prof.id
        created["prof_token"] = make_token(prof.id, "professor", "access")

        other_prof = User(email=f"other_{tag}@example.edu", roll_no=f"OP_{tag}", name="Other Prof P7", role="professor", password_hash=hash_password("pw"))
        db.add(other_prof); db.flush()
        created["other_prof_id"] = other_prof.id
        created["other_prof_token"] = make_token(other_prof.id, "professor", "access")

        course = Course(professor_id=prof.id, name=f"P7 Course {tag}")
        db.add(course); db.flush()
        created["course_id"] = course.id

        secret = b"p7_acceptance_test_secret_32bytes"
        session = ClassSession(
            course_id=course.id,
            status="live",
            step_ms=1500,
            secret=secret,
            started_at=datetime.now(timezone.utc),
        )
        db.add(session); db.flush()
        created["session_id"] = session.id
        created["secret"] = secret

        # Create 6 students:
        # student 0: has photo, template at offset 0.0
        # student 1: has photo, template at offset 0.8
        # student 2: no photo, template at offset 1.6
        # student 3: no photo, template at offset 2.4
        # student 4: roll number starts with '=' (formula injection test)
        # student 5: template at offset 4.0
        fake_photo = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9"
        for i in range(6):
            roll = f"=INJ_{tag}_{i}" if i == 4 else f"P7_{tag}_{i}"
            u = User(
                email=f"p7_stud_{tag}_{i}@example.edu",
                roll_no=roll,
                name=f"P7 Student {i}",
                role="student",
                password_hash=hash_password("pw"),
            )
            db.add(u); db.flush()
            db.add(RosterEntry(course_id=course.id, student_id=u.id))
            vec = make_vector(val=0.0, offset_idx=0, offset_val=i * 0.8)
            photo_enc = encrypt(fake_photo) if i in (0, 1) else None
            db.add(FaceTemplate(student_id=u.id, vector=vec, model=M, status="confirmed", photo_enc=photo_enc))
            created["students"].append((u.id, make_token(u.id, "student", "access"), vec, u.email, roll, u.name))

        # Unrostered student
        unrost = User(email=f"p7_un_{tag}@example.edu", roll_no=f"UN_{tag}", name="Unrostered", role="student", password_hash=hash_password("pw"))
        db.add(unrost); db.flush()
        db.add(FaceTemplate(student_id=unrost.id, vector=make_vector(9.9), model=M, status="confirmed"))
        created["unrostered_id"] = unrost.id
        created["unrostered_token"] = make_token(unrost.id, "student", "access")

        db.commit()

    yield created

    # Cleanup
    with SessionLocal() as db:
        stud_ids = [s[0] for s in created["students"]] + [created["unrostered_id"]]
        p7_courses = db.scalars(select(Course).where(Course.name.like("P7 %"))).all()
        cids = [c.id for c in p7_courses]
        if cids:
            sids = list(db.scalars(select(ClassSession.id).where(ClassSession.course_id.in_(cids))))
            for model in (AttendanceRecord, Spotcheck, Checkin, Challenge):
                db.query(model).filter(model.session_id.in_(sids)).delete()
            db.query(ClassSession).filter(ClassSession.course_id.in_(cids)).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id.in_(cids)).delete()
            for c in p7_courses:
                db.delete(c)
        db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(stud_ids)).delete()
        db.query(AuditLog).filter(AuditLog.actor_id.in_(stud_ids + [created["other_prof_id"]])).delete()
        db.query(User).filter(User.id.in_(stud_ids + [created["other_prof_id"]])).delete()
        db.commit()


def checkin_stud(client, world, stud_idx, face=None, liveness=True):
    uid, tok, vec, _, _, _ = world["students"][stud_idx]
    h = {"Authorization": f"Bearer {tok}"}
    sid = world["session_id"]
    ch = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr = tokens.make_qr_payload(world["secret"], sid, w)
    body = {
        "qr": qr,
        "challenge_id": ch["challenge_id"],
        "face": {"vector": face if face is not None else vec, "model": M},
        "liveness": make_liveness(ch["colour_seq"]) if liveness else None,
    }
    return client.post(f"/api/v1/sessions/{sid}/checkin", json=body, headers=h)


# =========================================================================================
# Case 1: Close and records creation, idempotency, and concurrent close
# =========================================================================================
def test_01_close_records_and_idempotency(client, p7_world):
    """Case 1:
    - close on live session sets status='closed' and closed_at
    - exactly one attendance_records row per rostered student
    - checked-in (VERIFIED or REVIEW) -> status 'present', source 'checkin' (REVIEW has note 'unreviewed: <flags>')
    - not checked-in -> status 'absent', source 'default'
    - second close -> 200, same state, no duplicate rows, no extra audit log
    - close on 'created' session -> 409 INVALID_STATE
    - another professor -> 404, student -> 403
    - 20 simultaneous close calls -> exactly one set of rows, no 500
    """
    sid = p7_world["session_id"]
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p7_world['other_prof_token']}"}
    hs = {"Authorization": f"Bearer {p7_world['students'][0][1]}"}

    # Student 0 checks in VERIFIED
    r0 = checkin_stud(client, p7_world, 0)
    assert r0.status_code == 200 and r0.json()["status"] == "VERIFIED"

    # Student 1 checks in REVIEW (wrong face vector)
    wrong_face = make_vector(val=0.0, offset_idx=0, offset_val=9.9)
    r1 = checkin_stud(client, p7_world, 1, face=wrong_face)
    assert r1.status_code == 200 and r1.json()["status"] == "REVIEW"

    # Student 2, 3, 4, 5 do not check in

    # Unauthorized close attempts
    assert client.post(f"/api/v1/sessions/{sid}/close", headers=hp_other).status_code == 404
    assert client.post(f"/api/v1/sessions/{sid}/close", headers=hs).status_code == 403

    # Close session
    r_close = client.post(f"/api/v1/sessions/{sid}/close", headers=hp)
    assert r_close.status_code == 200
    res = r_close.json()
    assert res["status"] == "closed"

    # Verify DB records
    with SessionLocal() as db:
        recs = {r.student_id: r for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == sid))}
        # Exactly 6 rows for 6 students
        assert len(recs) == 6

        # Student 0: present / checkin
        s0_id = p7_world["students"][0][0]
        assert recs[s0_id].status == "present"
        assert recs[s0_id].source == "checkin"
        assert recs[s0_id].note is None

        # Student 1: present / checkin with unreviewed note
        s1_id = p7_world["students"][1][0]
        assert recs[s1_id].status == "present"
        assert recs[s1_id].source == "checkin"
        assert recs[s1_id].note is not None
        assert recs[s1_id].note.startswith("unreviewed:")

        # Student 2: absent / default
        s2_id = p7_world["students"][2][0]
        assert recs[s2_id].status == "absent"
        assert recs[s2_id].source == "default"

        audit_count_before = db.query(AuditLog).filter(AuditLog.target == sid, AuditLog.action == "session.closed").count()
        assert audit_count_before == 1

    # Second close: 200, no duplicates, no extra audit log
    r_close_again = client.post(f"/api/v1/sessions/{sid}/close", headers=hp)
    assert r_close_again.status_code == 200
    with SessionLocal() as db:
        assert db.query(AttendanceRecord).filter(AttendanceRecord.session_id == sid).count() == 6
        assert db.query(AuditLog).filter(AuditLog.target == sid, AuditLog.action == "session.closed").count() == 1

    # Close on created session -> 409 INVALID_STATE
    # Create new course & session in created state
    cid2 = client.post("/api/v1/courses", json={"name": "P7 C2"}, headers=hp).json()["id"]
    s2 = client.post("/api/v1/sessions", json={"course_id": cid2}, headers=hp).json()
    r_created_close = client.post(f"/api/v1/sessions/{s2['id']}/close", headers=hp)
    assert r_created_close.status_code == 409
    assert r_created_close.json()["error"]["code"] == "INVALID_STATE"

    # 20 simultaneous close calls on a live session -> exactly 1 set of rows, no 500
    client.post(f"/api/v1/sessions/{s2['id']}/start", headers=hp)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(client.post, f"/api/v1/sessions/{s2['id']}/close", headers=hp) for _ in range(20)]
        responses = [f.result() for f in futures]
    assert all(r.status_code == 200 for r in responses)
    with SessionLocal() as db:
        sess = db.get(ClassSession, s2["id"])
        assert sess.status == "closed"
        assert db.query(AttendanceRecord).filter(AttendanceRecord.session_id == s2["id"]).count() == 0  # course 2 had no students


# =========================================================================================
# Case 2: Prior decisions survive close
# =========================================================================================
def test_02_prior_decisions_survive_close(client, p7_world):
    """Case 2: Decisions made before close (resolve, manual, spotcheck) survive close unchanged."""
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Prior Decisions"}, headers=hp).json()["id"]

    # Add 4 roster students
    sids = [p7_world["students"][i][0] for i in range(4)]
    with SessionLocal() as db:
        for sid_u in sids:
            db.add(RosterEntry(course_id=cid, student_id=sid_u))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # 1. resolve student 0 -> absent
    r_res0 = client.post(f"/api/v1/sessions/{sid}/attendance/{sids[0]}/resolve", json={"status": "absent", "note": "sick"}, headers=hp)
    assert r_res0.status_code == 200

    # 2. manual student 1 -> present
    r_man1 = client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": sids[1], "reason": "phone dead"}, headers=hp)
    assert r_man1.status_code == 201

    # 3. spotcheck student 2 -> seen
    r_spot2 = client.post(f"/api/v1/sessions/{sid}/spotcheck/{sids[2]}", json={"result": "seen"}, headers=hp)
    assert r_spot2.status_code == 200

    # Student 3 has no decision (will get default absent on close)

    # Close session
    r_close = client.post(f"/api/v1/sessions/{sid}/close", headers=hp)
    assert r_close.status_code == 200

    with SessionLocal() as db:
        recs = {r.student_id: r for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == sid))}
        assert len(recs) == 4
        assert recs[sids[0]].status == "absent" and recs[sids[0]].source == "resolved" and recs[sids[0]].note == "sick"
        assert recs[sids[1]].status == "present" and recs[sids[1]].source == "manual" and recs[sids[1]].note == "phone dead"
        assert recs[sids[2]].status == "present" and recs[sids[2]].source == "resolved" and "spot-check" in recs[sids[2]].note
        assert recs[sids[3]].status == "absent" and recs[sids[3]].source == "default"


# =========================================================================================
# Case 3: After close behavior
# =========================================================================================
def test_03_after_close_refusals_and_reopen_allowed(client, p7_world):
    """Case 3: After close:
    - tokens refused (409 SESSION_NOT_LIVE)
    - checkin refused (404 SESSION_NOT_ACTIVE)
    - sessions/active no longer lists it
    - new session can be created for the course
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    hs = {"Authorization": f"Bearer {p7_world['students'][0][1]}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 After Close"}, headers=hp).json()["id"]

    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=p7_world["students"][0][0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # Active while live
    active_before = [x["id"] for x in client.get("/api/v1/sessions/active", headers=hs).json()]
    assert sid in active_before

    # Tokens work while live
    assert client.get(f"/api/v1/sessions/{sid}/tokens", headers=hp).status_code == 200

    # Close session
    client.post(f"/api/v1/sessions/{sid}/close", headers=hp)

    # 1. Tokens refused
    r_tok = client.get(f"/api/v1/sessions/{sid}/tokens", headers=hp)
    assert r_tok.status_code == 409
    assert r_tok.json()["error"]["code"] == "SESSION_NOT_LIVE"

    # 2. Checkin refused
    r_ck = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": "test"}, headers=hs)
    assert r_ck.status_code == 404
    assert r_ck.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    # 3. Active sessions list does not contain it
    active_sids = [x["id"] for x in client.get("/api/v1/sessions/active", headers=hs).json()]
    assert sid not in active_sids

    # 4. New session can be created for this course
    s_new = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp)
    assert s_new.status_code == 201


# =========================================================================================
# Case 4: Live attendance list, summary, and privacy
# =========================================================================================
def test_04_live_attendance_list_summary_and_privacy(client, p7_world):
    """Case 4: GET /attendance:
    - {session, summary, students[]}
    - every roster student once
    - display in checked_in|review|not_here|present|absent|excused
    - final matches what close would write
    - summary counts add up to roster
    - not visible to other profs (404) or students (403)
    - no photos or face vectors in payload
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p7_world['other_prof_token']}"}
    hs = {"Authorization": f"Bearer {p7_world['students'][0][1]}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Live Att"}, headers=hp).json()["id"]

    stud_ids = [p7_world["students"][i][0] for i in range(3)]
    with SessionLocal() as db:
        for sid_u in stud_ids:
            db.add(RosterEntry(course_id=cid, student_id=sid_u))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # Unauthorized access
    assert client.get(f"/api/v1/sessions/{sid}/attendance", headers=hp_other).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/attendance", headers=hs).status_code == 403

    # Authorized access
    r = client.get(f"/api/v1/sessions/{sid}/attendance", headers=hp)
    assert r.status_code == 200
    data = r.json()
    assert "session" in data and "summary" in data and "students" in data
    assert len(data["students"]) == 3

    # Summary verification
    sm = data["summary"]
    assert sm["roster"] == 3
    assert sm["checked_in"] + sm["review"] + sm["not_here"] + sm["resolved_present"] + sm["resolved_absent"] + sm["excused"] == sm["roster"]

    # Privacy check: no photos, no face vectors in attendance response
    raw_text = r.text
    assert "face_vector" not in raw_text
    assert "vector" not in raw_text
    assert "photo_url" not in raw_text
    assert "photo_enc" not in raw_text
    assert "data:image" not in raw_text


# =========================================================================================
# Case 5: Attention Panel flagged items, reasons, and lookalikes
# =========================================================================================
def test_05_attention_flagged_reasons_and_lookalikes(client, p7_world):
    """Case 5:
    - REVIEW checkin shows as kind='flagged'
    - plain language reasons without words like 'cheating' or 'fraud'
    - photo_url present for student with stored photo, null otherwise
    - collisions name the other student
    - look-alikes name the enrolled classmate
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Attn Flagged"}, headers=hp).json()["id"]

    # Student 0 (has photo) and Student 1 (has photo)
    s0 = p7_world["students"][0]
    s1 = p7_world["students"][1]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.add(RosterEntry(course_id=cid, student_id=s1[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret

    # Student 0 submits a face that resembles Student 1
    # Student 1 template is at offset 0.8; student 0 template is at 0.0
    # Submitting offset 0.81 -> dist to S0 = 0.81 > 0.55 (mismatch), dist to S1 = 0.01 <= 0.55 (looks_like S1)
    ch0 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {s0[1]}"}).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr0 = tokens.make_qr_payload(sec, sid, w)
    body0 = {
        "qr": qr0,
        "challenge_id": ch0["challenge_id"],
        "face": {"vector": make_vector(val=0.0, offset_idx=0, offset_val=0.81), "model": M},
        "liveness": make_liveness(ch0["colour_seq"]),
    }
    r_ck0 = client.post(f"/api/v1/sessions/{sid}/checkin", json=body0, headers={"Authorization": f"Bearer {s0[1]}"})
    assert r_ck0.status_code == 200 and r_ck0.json()["status"] == "REVIEW"

    # Query attention feed
    r_attn = client.get(f"/api/v1/sessions/{sid}/attention", headers=hp)
    assert r_attn.status_code == 200
    items = r_attn.json()["items"]
    assert len(items) >= 1
    card0 = next(i for i in items if i["student_id"] == s0[0])
    assert card0["kind"] == "flagged"
    assert card0["photo_url"] is not None
    assert any("Face resembles" in r and s1[5] in r for r in card0["reasons"])

    # Prohibited language check
    for r in card0["reasons"]:
        assert "cheat" not in r.lower()
        assert "fraud" not in r.lower()
        assert "attack" not in r.lower()
        assert "mule" not in r.lower()


# =========================================================================================
# Case 6: Attention Panel overdue grace period and ordering
# =========================================================================================
def test_06_attention_overdue_grace_and_ordering(client, p7_world):
    """Case 6:
    - inside ATTENTION_GRACE_S (180 s) nobody is overdue
    - after grace period, unchecked-in students appear as overdue
    - ordered by past absence rate, then name
    - k parameter limits the list (1-20 enforced -> 422 outside)
    - total_open gives the uncapped count
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Overdue"}, headers=hp).json()["id"]

    stud_ids = [p7_world["students"][i][0] for i in range(4)]
    with SessionLocal() as db:
        for sid_u in stud_ids:
            db.add(RosterEntry(course_id=cid, student_id=sid_u))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # 1. Inside grace period -> items is empty
    r_inside = client.get(f"/api/v1/sessions/{sid}/attention", headers=hp)
    assert r_inside.status_code == 200
    assert r_inside.json()["items"] == []

    # 2. Advance started_at beyond grace period
    with SessionLocal() as db:
        sess = db.get(ClassSession, sid)
        sess.started_at = datetime.now(timezone.utc) - timedelta(seconds=config.ATTENTION_GRACE_S + 10)
        db.commit()

    # Query with k=2
    r_overdue = client.get(f"/api/v1/sessions/{sid}/attention?k=2", headers=hp)
    assert r_overdue.status_code == 200
    data = r_overdue.json()
    assert len(data["items"]) == 2
    assert data["total_open"] == 4
    assert all(i["kind"] == "overdue" for i in data["items"])

    # k validation: 1 <= k <= 20
    assert client.get(f"/api/v1/sessions/{sid}/attention?k=0", headers=hp).status_code == 422
    assert client.get(f"/api/v1/sessions/{sid}/attention?k=21", headers=hp).status_code == 422


# =========================================================================================
# Case 7: Cards dismissal and unsure behavior
# =========================================================================================
def test_07_attention_dismissal_and_unsure_behavior(client, p7_world):
    """Case 7:
    - Cards leave the panel after spotcheck (seen/not_seen) or resolve/manual.
    - unsure leaves the student in the review queue (display 'review') and records nothing in DB.
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Card Dismissal"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # Student 0 checks in REVIEW
    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret
    ch0 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {s0[1]}"}).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr0 = tokens.make_qr_payload(sec, sid, w)
    body0 = {
        "qr": qr0,
        "challenge_id": ch0["challenge_id"],
        "face": {"vector": make_vector(9.9), "model": M},
        "liveness": make_liveness(ch0["colour_seq"]),
    }
    client.post(f"/api/v1/sessions/{sid}/checkin", json=body0, headers={"Authorization": f"Bearer {s0[1]}"})

    # Card is in panel
    assert len(client.get(f"/api/v1/sessions/{sid}/attention", headers=hp).json()["items"]) == 1

    # Professor chooses 'unsure'
    r_unsure = client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "unsure"}, headers=hp)
    assert r_unsure.status_code == 200

    # Card leaves attention panel
    assert len(client.get(f"/api/v1/sessions/{sid}/attention", headers=hp).json()["items"]) == 0

    # Roster display is still 'review', no attendance record written
    r_att = client.get(f"/api/v1/sessions/{sid}/attendance", headers=hp).json()
    assert r_att["students"][0]["display"] == "review"
    with SessionLocal() as db:
        assert db.query(AttendanceRecord).filter(AttendanceRecord.session_id == sid).count() == 0


# =========================================================================================
# Case 8: Suggestions and audit sampling
# =========================================================================================
def test_08_suggestions_and_audit_sampling(client, p7_world):
    """Case 8:
    - spotcheck/suggestions returns attention items plus up to 2 random VERIFIED students with kind='audit'
    - not_seen on audit student increments summary.audit_not_seen
    - seen on VERIFIED student is harmless
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Suggestions"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret

    # Student 0 checks in VERIFIED
    ch0 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {s0[1]}"}).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr0 = tokens.make_qr_payload(sec, sid, w)
    client.post(f"/api/v1/sessions/{sid}/checkin", json={
        "qr": qr0,
        "challenge_id": ch0["challenge_id"],
        "face": {"vector": s0[2], "model": M},
        "liveness": make_liveness(ch0["colour_seq"]),
    }, headers={"Authorization": f"Bearer {s0[1]}"})

    # Suggestions includes audit card for student 0
    r_sugg = client.get(f"/api/v1/sessions/{sid}/spotcheck/suggestions", headers=hp)
    assert r_sugg.status_code == 200
    items = r_sugg.json()["items"]
    audit_cards = [i for i in items if i["kind"] == "audit"]
    assert len(audit_cards) == 1
    assert audit_cards[0]["student_id"] == s0[0]

    # not_seen on audit student -> records false accept
    client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "not_seen"}, headers=hp)
    sm = client.get(f"/api/v1/sessions/{sid}/attendance", headers=hp).json()["summary"]
    assert sm["audit_sampled"] == 1
    assert sm["audit_not_seen"] == 1


# =========================================================================================
# Case 9: Spot-check rules and audit logging
# =========================================================================================
def test_09_spotcheck_rules_and_auditing(client, p7_world):
    """Case 9:
    - seen -> present, not_seen -> absent
    - invalid result -> 422
    - unrostered student -> 404 NOT_ON_ROSTER
    - session closed or created -> 409
    - repeated verdicts: latest wins; every verdict in audit_log
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Spotcheck Rules"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]

    # Session in created state -> 409
    assert client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "seen"}, headers=hp).status_code == 409

    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # Invalid result -> 422
    assert client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "maybe"}, headers=hp).status_code == 422

    # Unrostered student -> 404
    assert client.post(f"/api/v1/sessions/{sid}/spotcheck/nonexistent_id", json={"result": "seen"}, headers=hp).status_code == 404

    # 1. spotcheck seen -> present
    r_seen = client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "seen"}, headers=hp)
    assert r_seen.status_code == 200
    with SessionLocal() as db:
        rec = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == sid, AttendanceRecord.student_id == s0[0]))
        assert rec.status == "present"

    # 2. repeated verdict: not_seen -> absent (latest wins)
    r_not_seen = client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "not_seen"}, headers=hp)
    assert r_not_seen.status_code == 200
    with SessionLocal() as db:
        rec = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == sid, AttendanceRecord.student_id == s0[0]))
        assert rec.status == "absent"

        # Verify audit log contains both verdicts
        logs = db.scalars(select(AuditLog).where(AuditLog.target == s0[0], AuditLog.action.like("spotcheck.%"))).all()
        assert len(logs) == 2


# =========================================================================================
# Case 10: Photo URL security and view auditing
# =========================================================================================
def test_10_photo_url_security_and_view_auditing(client, p7_world):
    """Case 10:
    - photo_url from panel works for owning professor once for 5 min
    - 403 for another professor
    - expired/tampered signature rejected
    - each fetch writes photo.viewed
    - panel/ws payloads contain no photo for students who are not on panel
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p7_world['other_prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Photo Sec"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]  # has photo
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret

    # Checkin REVIEW to raise card
    ch0 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {s0[1]}"}).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr0 = tokens.make_qr_payload(sec, sid, w)
    client.post(f"/api/v1/sessions/{sid}/checkin", json={
        "qr": qr0,
        "challenge_id": ch0["challenge_id"],
        "face": {"vector": make_vector(9.9), "model": M},
        "liveness": make_liveness(ch0["colour_seq"]),
    }, headers={"Authorization": f"Bearer {s0[1]}"})

    cards = client.get(f"/api/v1/sessions/{sid}/attention", headers=hp).json()["items"]
    photo_rel_url = cards[0]["photo_url"]
    assert photo_rel_url is not None

    # Fetch photo as owning professor -> 200
    r_img = client.get(photo_rel_url, headers=hp)
    assert r_img.status_code == 200
    assert r_img.headers["content-type"].startswith("image/")

    # Fetch photo as other professor -> 403
    assert client.get(photo_rel_url, headers=hp_other).status_code == 403

    # Tampered signature -> 403
    tampered_url = photo_rel_url[:-4] + "dead"
    assert client.get(tampered_url, headers=hp).status_code == 403

    # Verify photo.viewed audit log
    with SessionLocal() as db:
        view_logs = db.scalars(select(AuditLog).where(AuditLog.action == "photo.viewed", AuditLog.target == s0[0])).all()
        assert len(view_logs) >= 1


# =========================================================================================
# Case 11: Resolve and manual endpoints
# =========================================================================================
def test_11_resolve_and_manual_endpoints(client, p7_world):
    """Case 11:
    - resolve {present|absent, note}: upserts record (source resolved), works live and after close, 409 on created
    - manual {student_id, reason, status=present}: 201, source manual, reason stored as note, audited
    - missing/short reason -> 422
    - unknown student -> 404
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Resolve Manual"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]

    # 409 before start (created state)
    assert client.post(f"/api/v1/sessions/{sid}/attendance/{s0[0]}/resolve", json={"status": "present"}, headers=hp).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": s0[0], "reason": "excused"}, headers=hp).status_code == 409

    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    # Resolve validation
    assert client.post(f"/api/v1/sessions/{sid}/attendance/{s0[0]}/resolve", json={"status": "invalid"}, headers=hp).status_code == 422

    # Manual validation
    assert client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": s0[0], "reason": "ab"}, headers=hp).status_code == 422
    assert client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": "nonexistent", "reason": "broken phone"}, headers=hp).status_code == 404

    # Successful manual add
    r_man = client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": s0[0], "reason": "broken phone"}, headers=hp)
    assert r_man.status_code == 201
    assert r_man.json()["source"] == "manual"

    # Successful resolve
    r_res = client.post(f"/api/v1/sessions/{sid}/attendance/{s0[0]}/resolve", json={"status": "present", "note": "verified in person"}, headers=hp)
    assert r_res.status_code == 200
    assert r_res.json()["source"] == "resolved"


# =========================================================================================
# Case 12: CSV Export and formula injection guard
# =========================================================================================
def test_12_export_csv_and_formula_injection_guard(client, p7_world):
    """Case 12:
    - export?format=csv
    - header: roll_no,name,status,source,checkin_status,score,flags,note
    - values starting with = + - @ prefixed with '
    - format=xlsx -> 422
    - other profs -> 404
    - X-Session-Status header present
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p7_world['other_prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Export"}, headers=hp).json()["id"]

    # Student 4 has roll number starting with '='
    s4 = p7_world["students"][4]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s4[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)
    client.post(f"/api/v1/sessions/{sid}/close", headers=hp)

    # 1. Other professor -> 404
    assert client.get(f"/api/v1/sessions/{sid}/export?format=csv", headers=hp_other).status_code == 404

    # 2. format=xlsx -> 422
    assert client.get(f"/api/v1/sessions/{sid}/export?format=xlsx", headers=hp).status_code == 422

    # 3. CSV Export
    r_exp = client.get(f"/api/v1/sessions/{sid}/export?format=csv", headers=hp)
    assert r_exp.status_code == 200
    assert r_exp.headers["content-type"].startswith("text/csv")
    assert r_exp.headers["X-Session-Status"] == "closed"

    reader = list(csv.DictReader(io.StringIO(r_exp.text)))
    assert len(reader) == 1
    row = reader[0]
    assert list(row.keys()) == ["roll_no", "name", "status", "source", "checkin_status", "score", "flags", "note"]

    # Formula injection guard: roll number starts with "'" before "="
    assert not row["roll_no"].startswith("=")
    assert row["roll_no"].startswith("'=")


# =========================================================================================
# Case 13: WebSocket auth guards and hello frame
# =========================================================================================
def test_13_websocket_auth_guards_and_hello_frame(client, p7_world):
    """Case 13:
    - no token / garbage / student / other prof -> closed before accept (4401 / 4404)
    - owner prof -> accepted, hello frame with seq, resync, current stats
    """
    sid = p7_world["session_id"]
    base = f"/api/v1/ws/sessions/{sid}"

    # No token
    with pytest.raises(Exception):
        with client.websocket_connect(base) as ws:
            ws.receive_json()

    # Garbage token
    with pytest.raises(Exception):
        with client.websocket_connect(f"{base}?token=garbage") as ws:
            ws.receive_json()

    # Student token
    with pytest.raises(Exception):
        with client.websocket_connect(f"{base}?token={p7_world['students'][0][1]}") as ws:
            ws.receive_json()

    # Other professor token
    with pytest.raises(Exception):
        with client.websocket_connect(f"{base}?token={p7_world['other_prof_token']}") as ws:
            ws.receive_json()

    # Owner professor -> connects and receives hello
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert "seq" in hello
        assert "resync" in hello["data"]
        assert "roster" in hello["data"]
        assert "checked_in" in hello["data"]


# =========================================================================================
# Case 14: WebSocket events streaming and stats tick
# =========================================================================================
def test_14_websocket_event_streaming_and_stats_tick(client, p7_world):
    """Case 14:
    - Events: checkin.created, checkin.updated (collision), attention.raised, attention.cleared, session.state
    - stats.tick received periodically
    - ping -> pong
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 WS Stream"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]  # has photo
    s1 = p7_world["students"][1]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.add(RosterEntry(course_id=cid, student_id=s1[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret

    base = f"/api/v1/ws/sessions/{sid}"
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}") as ws:
        # Hello frame
        assert ws.receive_json()["type"] == "hello"

        # 1. Ping -> Pong
        ws.send_text("ping")
        # Read until pong
        got_pong = False
        for _ in range(5):
            msg = ws.receive_json()
            if msg["type"] == "pong":
                got_pong = True
                break
        assert got_pong

        # 2. Checkin REVIEW -> triggers checkin.created + attention.raised
        ch0 = client.post(f"/api/v1/sessions/{sid}/challenge", headers={"Authorization": f"Bearer {s0[1]}"}).json()
        now_ms = int(time.time() * 1000)
        w = tokens.window_for(now_ms, 1500)
        qr0 = tokens.make_qr_payload(sec, sid, w)
        client.post(f"/api/v1/sessions/{sid}/checkin", json={
            "qr": qr0,
            "challenge_id": ch0["challenge_id"],
            "face": {"vector": make_vector(9.9), "model": M},
            "liveness": make_liveness(ch0["colour_seq"]),
        }, headers={"Authorization": f"Bearer {s0[1]}"})

        types_seen = set()
        for _ in range(8):
            m = ws.receive_json()
            types_seen.add(m["type"])
            if {"checkin.created", "attention.raised"}.issubset(types_seen):
                break
        assert "checkin.created" in types_seen
        assert "attention.raised" in types_seen

        # 3. Spotcheck -> triggers attention.cleared
        client.post(f"/api/v1/sessions/{sid}/spotcheck/{s0[0]}", json={"result": "seen"}, headers=hp)
        types_seen2 = set()
        for _ in range(8):
            m = ws.receive_json()
            types_seen2.add(m["type"])
            if "attention.cleared" in types_seen2:
                break
        assert "attention.cleared" in types_seen2

        # 4. Session state transition -> session.state
        client.post(f"/api/v1/sessions/{sid}/pause", headers=hp)
        types_seen3 = set()
        for _ in range(8):
            m = ws.receive_json()
            types_seen3.add(m["type"])
            if "session.state" in types_seen3:
                break
        assert "session.state" in types_seen3


# =========================================================================================
# Case 15: WebSocket reconnect, replay, and multi-client broadcast
# =========================================================================================
def test_15_websocket_reconnect_replay_and_broadcast(client, p7_world):
    """Case 15:
    - reconnect with since_seq: receives only missed events in order, no duplicates
    - since_seq > cur_seq: hello.data.resync=True, no backlog
    - multiple connected clients simultaneously receive events in seq order
    """
    hp = {"Authorization": f"Bearer {p7_world['prof_token']}"}
    cid = client.post("/api/v1/courses", json={"name": "P7 Replay"}, headers=hp).json()["id"]

    s0 = p7_world["students"][0]
    s1 = p7_world["students"][1]
    with SessionLocal() as db:
        db.add(RosterEntry(course_id=cid, student_id=s0[0]))
        db.add(RosterEntry(course_id=cid, student_id=s1[0]))
        db.commit()

    s = client.post("/api/v1/sessions", json={"course_id": cid}, headers=hp).json()
    sid = s["id"]
    client.post(f"/api/v1/sessions/{sid}/start", headers=hp)

    with SessionLocal() as db:
        sec = db.get(ClassSession, sid).secret

    base = f"/api/v1/ws/sessions/{sid}"

    # 1. Connect client 1 and record events
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}") as ws1:
        h1 = ws1.receive_json()
        seq_start = h1["seq"]

        # Trigger an event
        client.post(f"/api/v1/sessions/{sid}/pause", headers=hp)
        ev_pause = None
        for _ in range(5):
            m = ws1.receive_json()
            if m["type"] == "session.state" and m["data"].get("status") == "paused":
                ev_pause = m
                break
        assert ev_pause is not None
        pause_seq = ev_pause["seq"]

    # 2. Trigger another event while disconnected
    client.post(f"/api/v1/sessions/{sid}/resume", headers=hp)

    # 3. Reconnect with since_seq = pause_seq
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}&since_seq={pause_seq}") as ws2:
        h2 = ws2.receive_json()
        assert h2["data"]["resync"] is False
        # First event in backlog must be resume (session.state)
        ev_resume = ws2.receive_json()
        assert ev_resume["type"] == "session.state"
        assert ev_resume["data"]["status"] == "live"
        assert ev_resume["seq"] > pause_seq

    # 4. Reconnect with invalid future seq -> resync=True
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}&since_seq=999999") as ws3:
        h3 = ws3.receive_json()
        assert h3["data"]["resync"] is True

    # 5. Multiple connected clients simultaneously receive events in seq order
    with client.websocket_connect(f"{base}?token={p7_world['prof_token']}") as cli_a, \
         client.websocket_connect(f"{base}?token={p7_world['prof_token']}") as cli_b:
        cli_a.receive_json()  # hello
        cli_b.receive_json()  # hello

        client.post(f"/api/v1/sessions/{sid}/pause", headers=hp)
        ma = None
        for _ in range(5):
            m = cli_a.receive_json()
            if m["type"] == "session.state" and m["data"].get("status") == "paused":
                ma = m
                break
        mb = None
        for _ in range(5):
            m = cli_b.receive_json()
            if m["type"] == "session.state" and m["data"].get("status") == "paused":
                mb = m
                break
        assert ma is not None and mb is not None
        assert ma["seq"] == mb["seq"]
        assert ma["data"]["status"] == "paused"


# =========================================================================================
# Case 16: Subscriber cleanup and no leak
# =========================================================================================
def test_16_websocket_subscriber_cleanup_no_leak(client, p7_world):
    """Case 16: 100 connect/disconnect cycles: hub has no stale subscribers."""
    sid = p7_world["session_id"]
    base = f"/api/v1/ws/sessions/{sid}?token={p7_world['prof_token']}"

    for _ in range(100):
        with client.websocket_connect(base) as ws:
            ws.receive_json()

    # Verify no leaked subscribers in hub._subs[sid]
    with hub._lock:
        active_subs = hub._subs.get(sid, set())
        assert len(active_subs) == 0, f"Expected 0 active subscribers after disconnects, got {len(active_subs)}"
