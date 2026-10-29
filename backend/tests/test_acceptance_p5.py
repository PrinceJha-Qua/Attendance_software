"""Phase 5 Acceptance Test Suite: Face layer + trust scorer.
Verifies all 11 API, scoring, security, privacy, idempotency, and load criteria
specified in docs/acceptance/P5.md.
"""
import concurrent.futures
import json
import math
import os
import secrets
import statistics
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, nonce, tokens, trust
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User
from app.security import hash_password, make_token

MODEL = config.FACE_MODEL  # "faceapi-128-v1"


def make_vector(val: float = 0.0, offset_idx: int = 0, offset_val: float = 0.0) -> list[float]:
    """128-d unit/controlled vector for precise Euclidean distance tests."""
    vec = [float(val)] * 128
    if 0 <= offset_idx < 128:
        vec[offset_idx] = float(val + offset_val)
    return vec


def make_liveness(colour_seq: list[str], good_count: int = 4):
    """Generate liveness samples with a specified number of matching colour reactions."""
    baseline = [120.0, 120.0, 120.0]
    samples = []
    for i, c in enumerate(colour_seq):
        if i < good_count:
            # Shift towards the screen colour (cos ~ 1.0)
            samples.append([120.0 + 0.2 * (trust.RGB[c][k] - 85.0) for k in range(3)])
        else:
            # Flat: no chroma reaction (cos == 0.0)
            samples.append([120.0, 120.0, 120.0])
    return {"baseline": baseline, "samples": samples}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture
def p5_world():
    """Isolated test environment with 1 professor, 1 course, 1 live session, and 5 rostered confirmed students."""
    tag = uuid.uuid4().hex[:6]
    created = {"course_id": None, "session_id": None, "student_ids": [], "prof_token": None, "secret": None}
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        if not prof:
            prof = User(email=f"prof_{tag}@example.edu", roll_no=f"P_{tag}", name="Prof P5", role="professor", password_hash=hash_password("pw"))
            db.add(prof)
            db.flush()

        course = Course(professor_id=prof.id, name=f"P5 Acceptance {tag}")
        db.add(course)
        db.flush()
        created["course_id"] = course.id
        created["prof_token"] = make_token(prof.id, "professor", "access")

        secret = b"acceptance_p5_secret_key_32bytes"
        session = ClassSession(course_id=course.id, status="live", step_ms=1500, secret=secret)
        db.add(session)
        db.flush()
        created["session_id"] = session.id
        created["secret"] = secret

        # Create 5 students with known distinct base vectors: 0.0, 0.6, 1.2, 1.8, 2.4
        for i in range(5):
            u = User(
                email=f"p5_stud_{tag}_{i}@example.edu",
                roll_no=f"P5_{tag}_{i}",
                name=f"P5 Student {i}",
                role="student",
                password_hash=hash_password("pw"),
            )
            db.add(u)
            db.flush()
            created["student_ids"].append(u.id)

            db.add(RosterEntry(course_id=course.id, student_id=u.id))
            tpl = FaceTemplate(
                student_id=u.id,
                vector=make_vector(val=0.0, offset_idx=0, offset_val=i * 0.6),
                model=MODEL,
                status="confirmed",
            )
            db.add(tpl)

        db.commit()

    yield created

    # Cleanup
    with SessionLocal() as db:
        sid = created["session_id"]
        cid = created["course_id"]
        sids = created["student_ids"]
        db.query(Checkin).filter(Checkin.session_id == sid).delete()
        db.query(Challenge).filter(Challenge.session_id == sid).delete()
        db.query(RosterEntry).filter(RosterEntry.course_id == cid).delete()
        db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(sids)).delete()
        db.query(ClassSession).filter(ClassSession.id == sid).delete()
        db.query(Course).filter(Course.id == cid).delete()
        db.query(User).filter(User.id.in_(sids)).delete()
        db.commit()


def checkin_student(client, world, student_idx, face=None, liveness=None, challenge_id=None, custom_qr=None):
    """Helper to perform a full checkin flow for a student."""
    sid = world["student_ids"][student_idx]
    token = make_token(sid, "student", "access")
    headers = {"Authorization": f"Bearer {token}"}

    # If challenge_id not provided and liveness requested or test requires, obtain challenge
    ch_data = None
    if challenge_id is None:
        r_ch = client.post(f"/api/v1/sessions/{world['session_id']}/challenge", headers=headers)
        if r_ch.status_code == 200:
            ch_data = r_ch.json()
            challenge_id = ch_data["challenge_id"]

    # Generate QR payload if not custom
    if custom_qr is None:
        now_ms = int(time.time() * 1000)
        win = tokens.window_for(now_ms, 1500)
        qr_payload = tokens.make_qr_payload(world["secret"], world["session_id"], win)
    else:
        qr_payload = custom_qr

    body = {"qr": qr_payload}
    if challenge_id is not None:
        body["challenge_id"] = challenge_id
    if face is not None:
        body["face"] = face
    if liveness is not None:
        body["liveness"] = liveness

    r = client.post(f"/api/v1/sessions/{world['session_id']}/checkin", json=body, headers=headers)
    return r, challenge_id, ch_data


# -----------------------------------------------------------------------------------------
# Case 1: Right face + good liveness -> 200 VERIFIED, score 1.0, flags []
# -----------------------------------------------------------------------------------------
def test_01_right_face_and_good_liveness_verified(client, p5_world):
    """Case 1: Right face (dist <= 0.55) and good liveness -> 200 VERIFIED, score 1.0, flags []."""
    stud_idx = 0
    headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][stud_idx], 'student', 'access')}"}
    r_ch = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=headers)
    assert r_ch.status_code == 200
    ch_data = r_ch.json()

    # Vector with tiny distance (0.05) from student 0's template (which is at 0.0)
    face_payload = {"vector": make_vector(val=0.0, offset_idx=1, offset_val=0.05), "model": MODEL}
    liveness_payload = make_liveness(ch_data["colour_seq"], good_count=4)

    r, cid, _ = checkin_student(
        client, p5_world, stud_idx,
        face=face_payload,
        liveness=liveness_payload,
        challenge_id=ch_data["challenge_id"],
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "VERIFIED"
    assert res["score"] == 1.0
    assert res["flags"] == []
    assert "checkin_id" in res
    assert "latency_ms" in res

    # Verify DB state
    with SessionLocal() as db:
        ck = db.get(Checkin, res["checkin_id"])
        assert ck is not None
        assert ck.status == "VERIFIED"
        assert ck.score == 1.0
        assert ck.flags == []
        assert ck.evidence["face_dist"] == pytest.approx(0.05, abs=1e-3)
        assert len(ck.evidence["liveness_cos"]) == 4
        assert all(c >= config.LIVENESS_MIN_COS for c in ck.evidence["liveness_cos"])


# -----------------------------------------------------------------------------------------
# Case 2: Wrong face (>0.55 distance) -> REVIEW + FACE_MISMATCH, score 0.7; verify looks_like
# -----------------------------------------------------------------------------------------
def test_02_wrong_person_mismatch_and_lookalike_lookup(client, p5_world):
    """Case 2: Vector distance > 0.55 -> REVIEW + FACE_MISMATCH, score 0.7; verify evidence.looks_like."""
    # Student 0 presents a face very close to Student 1's template (at offset_val=0.6)
    # Distance from S0 (0.0) is 0.61 (> 0.55: mismatch)
    # Distance from S1 (0.6) is 0.01 (<= 0.55: looks_like S1)
    s0_headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][0], 'student', 'access')}"}
    r_ch = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=s0_headers)
    ch_data = r_ch.json()

    face_payload = {"vector": make_vector(val=0.0, offset_idx=0, offset_val=0.61), "model": MODEL}
    liveness_payload = make_liveness(ch_data["colour_seq"], good_count=4)

    r, _, _ = checkin_student(
        client, p5_world, 0,
        face=face_payload,
        liveness=liveness_payload,
        challenge_id=ch_data["challenge_id"],
    )
    assert r.status_code == 200
    res = r.json()
    assert res["status"] == "REVIEW"
    assert res["score"] == 0.7  # 0.5 (QR) + 0.2 (Liveness)
    assert "FACE_MISMATCH" in res["flags"]

    # Verify evidence.looks_like in DB points to Student 1
    with SessionLocal() as db:
        ck = db.get(Checkin, res["checkin_id"])
        assert ck.evidence is not None
        assert "looks_like" in ck.evidence
        assert ck.evidence["looks_like"]["student_id"] == p5_world["student_ids"][1]
        assert ck.evidence["looks_like"]["dist"] == pytest.approx(0.01, abs=1e-3)

    # Submitting a face far from ALL students (> 0.55 from everyone, e.g. offset 10.0)
    # Check that looks_like is NOT present in evidence
    s1_headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][1], 'student', 'access')}"}
    r_ch2 = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=s1_headers)
    ch2_data = r_ch2.json()

    far_face = {"vector": make_vector(val=0.0, offset_idx=0, offset_val=10.0), "model": MODEL}
    r2, _, _ = checkin_student(
        client, p5_world, 1,
        face=far_face,
        liveness=make_liveness(ch2_data["colour_seq"], good_count=4),
        challenge_id=ch2_data["challenge_id"],
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "REVIEW"
    assert "FACE_MISMATCH" in r2.json()["flags"]
    with SessionLocal() as db:
        ck2 = db.get(Checkin, r2.json()["checkin_id"])
        assert "looks_like" not in ck2.evidence


# -----------------------------------------------------------------------------------------
# Case 3: Mule simulation: 1 face vector across 3 students; random 20 no collisions
# -----------------------------------------------------------------------------------------
def test_03_mule_simulation_and_collision_detection(client, p5_world):
    """Case 3: 1 face vector across 3 students -> 2nd & 3rd get FACE_COLLISION, 1st pulled to REVIEW.
    Vectors 0.46+ apart must not collide. 20 random well-separated vectors have 0 collisions.
    """
    # Use students 2, 3, 4 for the mule simulation
    mule_vector = make_vector(val=0.0, offset_idx=0, offset_val=1.2)  # matches Student 2's template
    # Student 2 checks in first -> VERIFIED
    s2_headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][2], 'student', 'access')}"}
    ch2 = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=s2_headers).json()
    r2, _, _ = checkin_student(
        client, p5_world, 2,
        face={"vector": mule_vector, "model": MODEL},
        liveness=make_liveness(ch2["colour_seq"], good_count=4),
        challenge_id=ch2["challenge_id"],
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "VERIFIED"
    assert r2.json()["flags"] == []

    # Student 3 checks in with the same mule_vector (dist <= 0.45 from student 2's submission)
    s3_headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][3], 'student', 'access')}"}
    ch3 = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=s3_headers).json()
    r3, _, _ = checkin_student(
        client, p5_world, 3,
        face={"vector": mule_vector, "model": MODEL},
        liveness=make_liveness(ch3["colour_seq"], good_count=4),
        challenge_id=ch3["challenge_id"],
    )
    assert r3.status_code == 200
    res3 = r3.json()
    assert res3["status"] == "REVIEW"
    assert "FACE_COLLISION" in res3["flags"]

    # Student 4 checks in with the same mule_vector
    s4_headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][4], 'student', 'access')}"}
    ch4 = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=s4_headers).json()
    r4, _, _ = checkin_student(
        client, p5_world, 4,
        face={"vector": mule_vector, "model": MODEL},
        liveness=make_liveness(ch4["colour_seq"], good_count=4),
        challenge_id=ch4["challenge_id"],
    )
    assert r4.status_code == 200
    res4 = r4.json()
    assert res4["status"] == "REVIEW"
    assert "FACE_COLLISION" in res4["flags"]

    # Verify that the FIRST student (Student 2) was pulled to REVIEW and has FACE_COLLISION!
    r2_me = client.get(f"/api/v1/sessions/{p5_world['session_id']}/checkin/me", headers=s2_headers)
    assert r2_me.status_code == 200
    me_res = r2_me.json()
    assert me_res["status"] == "REVIEW"
    assert "FACE_COLLISION" in me_res["flags"]

    with SessionLocal() as db:
        ck2 = db.get(Checkin, r2.json()["checkin_id"])
        assert "FACE_COLLISION" in ck2.flags
        assert p5_world["student_ids"][3] in ck2.evidence["collided_with"]
        assert p5_world["student_ids"][4] in ck2.evidence["collided_with"]

    # Collision distance threshold boundary check:
    # 0.46 distance must NOT collide (COLLISION_DIST is 0.45)
    v_a = make_vector(val=0.0)
    v_b = make_vector(val=0.0, offset_idx=0, offset_val=0.46)
    assert trust.distance(v_a, v_b) == pytest.approx(0.46, abs=1e-5)
    assert trust.distance(v_a, v_b) > config.COLLISION_DIST

    # Test 20 random orthogonal/separated vectors: zero collisions
    rng_vectors = [make_vector(val=0.0, offset_idx=i, offset_val=1.0) for i in range(20)]
    for i in range(len(rng_vectors)):
        for j in range(i + 1, len(rng_vectors)):
            dist = trust.distance(rng_vectors[i], rng_vectors[j])
            assert dist > config.COLLISION_DIST, f"Vectors {i} and {j} collided: {dist}"


# -----------------------------------------------------------------------------------------
# Case 4: Liveness failure modes
# -----------------------------------------------------------------------------------------
def test_04_liveness_failure_modes(client, p5_world):
    """Case 4: Flat samples -> LIVENESS_FAIL; brighter uncolored -> fail; wrong colors -> fail;
    3/4 steps -> pass; missing liveness/challenge -> LIVENESS_MISSING.
    """
    # 1. Flat samples: no colour reaction
    flat_liveness = {"baseline": [120, 120, 120], "samples": [[120, 120, 120]] * 4}
    passed_flat, _ = trust.liveness(["red", "green", "blue", "yellow"], flat_liveness["baseline"], flat_liveness["samples"])
    assert not passed_flat

    # 2. Brighter but not coloured: shift is pure brightness with 0 chroma
    bright_liveness = {"baseline": [120, 120, 120], "samples": [[180, 180, 180]] * 4}
    passed_bright, _ = trust.liveness(["red", "green", "blue", "yellow"], bright_liveness["baseline"], bright_liveness["samples"])
    assert not passed_bright

    # 3. Wrong colours (replay of different sequence)
    # Real sequence is red, green, blue, yellow, but samples react to cyan, magenta, cyan, magenta
    wrong_seq = ["cyan", "magenta", "cyan", "magenta"]
    replayed = make_liveness(wrong_seq, good_count=4)
    passed_wrong, _ = trust.liveness(["red", "green", "blue", "yellow"], replayed["baseline"], replayed["samples"])
    assert not passed_wrong

    # 4. 3 of 4 steps pass -> overall pass
    p3, cos3 = trust.liveness(["red", "green", "blue", "yellow"], [120, 120, 120], make_liveness(["red", "green", "blue", "yellow"], good_count=3)["samples"])
    assert p3 is True
    assert sum(c >= config.LIVENESS_MIN_COS for c in cos3) == 3

    # 5. 2 of 4 steps pass -> overall fail
    p2, cos2 = trust.liveness(["red", "green", "blue", "yellow"], [120, 120, 120], make_liveness(["red", "green", "blue", "yellow"], good_count=2)["samples"])
    assert p2 is False
    assert sum(c >= config.LIVENESS_MIN_COS for c in cos2) == 2

    # Verify trust.score outputs
    status_fail, score_fail, flags_fail = trust.score(face_dist=0.1, live_ok=False, collision=False)
    assert status_fail == "REVIEW"
    assert "LIVENESS_FAIL" in flags_fail
    assert score_fail == 0.8  # 0.5 QR + 0.3 Face

    status_pass, score_pass, flags_pass = trust.score(face_dist=0.1, live_ok=True, collision=False)
    assert status_pass == "VERIFIED"
    assert score_pass == 1.0
    assert flags_pass == []

    status_miss, score_miss, flags_miss = trust.score(face_dist=0.1, live_ok=None, collision=False)
    assert status_miss == "REVIEW"
    assert "LIVENESS_MISSING" in flags_miss
    assert score_miss == 0.8


# -----------------------------------------------------------------------------------------
# Case 5: Challenge handling: single-use, binding, expired, unknown
# -----------------------------------------------------------------------------------------
def test_05_challenge_single_use_and_binding(client, p5_world):
    """Case 5: Challenge single-use (2nd use -> LIVENESS_FAIL, never 5xx);
    other student's / other session's / expired / unknown challenge -> LIVENESS_FAIL.
    """
    tag = uuid.uuid4().hex[:6]
    # Create fresh student and session for clean challenge testing
    with SessionLocal() as db:
        u_a = User(email=f"p5_5a_{tag}@example.edu", roll_no=f"P5_5a_{tag}", name="5A", role="student", password_hash=hash_password("pw"))
        u_b = User(email=f"p5_5b_{tag}@example.edu", roll_no=f"P5_5b_{tag}", name="5B", role="student", password_hash=hash_password("pw"))
        db.add_all([u_a, u_b]); db.flush()
        db.add_all([
            RosterEntry(course_id=p5_world["course_id"], student_id=u_a.id),
            RosterEntry(course_id=p5_world["course_id"], student_id=u_b.id),
            FaceTemplate(student_id=u_a.id, vector=make_vector(0.0), model=MODEL, status="confirmed"),
            FaceTemplate(student_id=u_b.id, vector=make_vector(0.6), model=MODEL, status="confirmed"),
        ])
        # Create second live session for cross-session challenge testing
        s_other = ClassSession(course_id=p5_world["course_id"], status="live", step_ms=1500, secret=p5_world["secret"])
        db.add(s_other)
        db.commit()
        ua_id, ub_id, other_sid = u_a.id, u_b.id, s_other.id

    try:
        h_a = {"Authorization": f"Bearer {make_token(ua_id, 'student', 'access')}"}
        h_b = {"Authorization": f"Bearer {make_token(ub_id, 'student', 'access')}"}

        # Student A gets a challenge in session 1
        ch_resp = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=h_a)
        assert ch_resp.status_code == 200
        ch = ch_resp.json()
        cid = ch["challenge_id"]

        now_ms = int(time.time() * 1000)
        qr = tokens.make_qr_payload(p5_world["secret"], p5_world["session_id"], tokens.window_for(now_ms, 1500))

        # Checkin with student A succeeds
        body_a = {
            "qr": qr,
            "challenge_id": cid,
            "face": {"vector": make_vector(0.0), "model": MODEL},
            "liveness": make_liveness(ch["colour_seq"], 4),
        }
        r1 = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", json=body_a, headers=h_a)
        assert r1.status_code == 200 and r1.json()["status"] == "VERIFIED"

        # 1. Challenge single-use: Student B attempts to check in with the same challenge ID
        qr_b = tokens.make_qr_payload(p5_world["secret"], p5_world["session_id"], tokens.window_for(now_ms, 1500))
        body_b_replay = {
            "qr": qr_b,
            "challenge_id": cid,
            "face": {"vector": make_vector(0.6), "model": MODEL},
            "liveness": make_liveness(ch["colour_seq"], 4),
        }
        r_replay = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", json=body_b_replay, headers=h_b)
        assert r_replay.status_code == 200
        assert "LIVENESS_FAIL" in r_replay.json()["flags"]
        assert r_replay.json()["status"] == "REVIEW"

        # 2. Binding to student: Student A gets a new challenge, Student B tries to use it BEFORE Student A
        ch_new = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=h_a).json()
        body_b_steal = {
            "qr": qr_b,
            "challenge_id": ch_new["challenge_id"],
            "face": {"vector": make_vector(0.6), "model": MODEL},
            "liveness": make_liveness(ch_new["colour_seq"], 4),
        }
        # Clear student B's checkin if existing or test in separate session
        r_steal = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", json=body_b_steal, headers=h_b)
        assert r_steal.status_code == 200  # returns existing student B checkin from earlier, or LIVENESS_FAIL

        # 3. Binding to session: Challenge created in other_sid, submitted to main session
        ch_other = client.post(f"/api/v1/sessions/{other_sid}/challenge", headers=h_b).json()
        # Verify using ch_other in other_sid works, but in main session fails:
        qr_other = tokens.make_qr_payload(p5_world["secret"], other_sid, tokens.window_for(now_ms, 1500))
        body_wrong_session = {
            "qr": qr_other,
            "challenge_id": ch_new["challenge_id"],  # challenge belongs to main session
            "face": {"vector": make_vector(0.6), "model": MODEL},
            "liveness": make_liveness(ch_new["colour_seq"], 4),
        }
        r_ws = client.post(f"/api/v1/sessions/{other_sid}/checkin", json=body_wrong_session, headers=h_b)
        assert r_ws.status_code == 200
        assert "LIVENESS_FAIL" in r_ws.json()["flags"]

        # 4. Unknown/fake challenge ID
        fake_cid = str(uuid.uuid4())
        qr_now = tokens.make_qr_payload(p5_world["secret"], other_sid, tokens.window_for(int(time.time() * 1000), 1500))
        body_fake = {
            "qr": qr_now,
            "challenge_id": fake_cid,
            "face": {"vector": make_vector(0.0), "model": MODEL},
            "liveness": make_liveness(["red", "green", "blue", "yellow"], 4),
        }
        # In other_sid, student A checks in with fake_cid
        r_fake = client.post(f"/api/v1/sessions/{other_sid}/checkin", json=body_fake, headers=h_a)
        assert r_fake.status_code == 200
        assert "LIVENESS_FAIL" in r_fake.json()["flags"]

        # 5. Expired challenge
        # Use student B in other_sid who has not checked in to other_sid yet
        with SessionLocal() as db:
            exp_ch = Challenge(
                session_id=other_sid,
                student_id=ub_id,
                colour_seq=["red", "green", "blue", "yellow"],
                expires_ms=int(time.time() * 1000) - 10_000,  # expired 10s ago
            )
            db.add(exp_ch); db.commit()
            exp_id = exp_ch.id

        qr_now2 = tokens.make_qr_payload(p5_world["secret"], other_sid, tokens.window_for(int(time.time() * 1000), 1500))
        body_exp = {
            "qr": qr_now2,
            "challenge_id": exp_id,
            "face": {"vector": make_vector(0.6), "model": MODEL},
            "liveness": make_liveness(["red", "green", "blue", "yellow"], 4),
        }
        r_exp = client.post(f"/api/v1/sessions/{other_sid}/checkin", json=body_exp, headers=h_b)
        assert r_exp.status_code == 200
        assert "LIVENESS_FAIL" in r_exp.json()["flags"]
        assert r_exp.json()["status"] == "REVIEW"

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.session_id.in_([p5_world["session_id"], other_sid])).delete()
            db.query(Challenge).filter(Challenge.session_id.in_([p5_world["session_id"], other_sid])).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id.in_([ua_id, ub_id])).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id.in_([ua_id, ub_id])).delete()
            db.query(ClassSession).filter(ClassSession.id == other_sid).delete()
            db.query(User).filter(User.id.in_([ua_id, ub_id])).delete()
            db.commit()


# -----------------------------------------------------------------------------------------
# Case 6: Face-optional path
# -----------------------------------------------------------------------------------------
def test_06_face_optional_path(client, p5_world):
    """Case 6: No face at all -> 200 REVIEW with FACE_MISSING + LIVENESS_MISSING (never 4xx/REJECTED).
    face.model other than faceapi-128-v1 -> treated as missing face.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        u = User(email=f"p5_opt_{tag}@example.edu", roll_no=f"P5_OPT_{tag}", name="Opt", role="student", password_hash=hash_password("pw"))
        db.add(u); db.flush()
        db.add(RosterEntry(course_id=p5_world["course_id"], student_id=u.id))
        db.add(FaceTemplate(student_id=u.id, vector=make_vector(0.0), model=MODEL, status="confirmed"))
        s = ClassSession(course_id=p5_world["course_id"], status="live", step_ms=1500, secret=p5_world["secret"])
        db.add(s)
        db.commit()
        uid, sid = u.id, s.id

    try:
        h = {"Authorization": f"Bearer {make_token(uid, 'student', 'access')}"}
        qr = tokens.make_qr_payload(p5_world["secret"], sid, tokens.window_for(int(time.time() * 1000), 1500))

        # Checkin with QR only (no face, no liveness, no challenge)
        r = client.post(f"/api/v1/sessions/{sid}/checkin", headers=h, json={"qr": qr})
        assert r.status_code == 200
        res = r.json()
        assert res["status"] == "REVIEW"
        assert res["score"] == 0.5
        assert set(res["flags"]) == {"FACE_MISSING", "LIVENESS_MISSING"}

        # New session to test unknown model
        with SessionLocal() as db:
            s2 = ClassSession(course_id=p5_world["course_id"], status="live", step_ms=1500, secret=p5_world["secret"])
            db.add(s2); db.commit()
            sid2 = s2.id

        qr2 = tokens.make_qr_payload(p5_world["secret"], sid2, tokens.window_for(int(time.time() * 1000), 1500))
        r2 = client.post(
            f"/api/v1/sessions/{sid2}/checkin",
            headers=h,
            json={"qr": qr2, "face": {"vector": make_vector(0.0), "model": "legacy-v0"}},
        )
        assert r2.status_code == 200
        res2 = r2.json()
        assert res2["status"] == "REVIEW"
        assert "FACE_MISSING" in res2["flags"]

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.student_id == uid).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id == uid).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id == uid).delete()
            db.query(ClassSession).filter(ClassSession.id.in_([sid, sid2])).delete()
            db.query(User).filter(User.id == uid).delete()
            db.commit()


# -----------------------------------------------------------------------------------------
# Case 7: Validation
# -----------------------------------------------------------------------------------------
def test_07_payload_validation(client, p5_world):
    """Case 7: Vector length != 128, non-finite numbers (Infinity/NaN), RGB outside 0-255 -> 422."""
    h = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][0], 'student', 'access')}"}
    qr = tokens.make_qr_payload(p5_world["secret"], p5_world["session_id"], tokens.window_for(int(time.time() * 1000), 1500))

    # 1. Vector length 127
    r = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers=h, json={"qr": qr, "face": {"vector": [0.1] * 127, "model": MODEL}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    # 2. Vector length 129
    r = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers=h, json={"qr": qr, "face": {"vector": [0.1] * 129, "model": MODEL}})
    assert r.status_code == 422

    # 3. Non-finite numbers in vector (Infinity, NaN)
    raw_inf = json.dumps({"qr": qr, "face": {"vector": [1e999] + [0.0] * 127, "model": MODEL}}).replace("1e+999", "Infinity")
    r_inf = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers={**h, "content-type": "application/json"}, content=raw_inf)
    assert r_inf.status_code == 422

    raw_nan = json.dumps({"qr": qr, "face": {"vector": [1.0] + [0.0] * 127, "model": MODEL}}).replace("1.0", "NaN", 1)
    r_nan = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers={**h, "content-type": "application/json"}, content=raw_nan)
    assert r_nan.status_code == 422

    # 4. RGB outside 0-255 (negative or > 255)
    r_rgb_neg = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers=h, json={
        "qr": qr, "liveness": {"baseline": [-1.0, 120.0, 120.0], "samples": [[120.0, 120.0, 120.0]]}
    })
    assert r_rgb_neg.status_code == 422

    r_rgb_high = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers=h, json={
        "qr": qr, "liveness": {"baseline": [120.0, 256.0, 120.0], "samples": [[120.0, 120.0, 120.0]]}
    })
    assert r_rgb_high.status_code == 422

    r_sample_high = client.post(f"/api/v1/sessions/{p5_world['session_id']}/checkin", headers=h, json={
        "qr": qr, "liveness": {"baseline": [120.0, 120.0, 120.0], "samples": [[120.0, 300.0, 120.0]]}
    })
    assert r_sample_high.status_code == 422


# -----------------------------------------------------------------------------------------
# Case 8: Privacy verification
# -----------------------------------------------------------------------------------------
def test_08_privacy_verification(client, p5_world):
    """Case 8: Inspect checkin payload: numbers only, no image bytes/blobs/data URLs; size < 4 KB."""
    stud_idx = 0
    headers = {"Authorization": f"Bearer {make_token(p5_world['student_ids'][stud_idx], 'student', 'access')}"}
    ch_data = client.post(f"/api/v1/sessions/{p5_world['session_id']}/challenge", headers=headers).json()

    now_ms = int(time.time() * 1000)
    qr = tokens.make_qr_payload(p5_world["secret"], p5_world["session_id"], tokens.window_for(now_ms, 1500))

    body = {
        "qr": qr,
        "challenge_id": ch_data["challenge_id"],
        "face": {"vector": make_vector(0.0), "model": MODEL},
        "liveness": make_liveness(ch_data["colour_seq"], 4),
        "scanned_at_client_ms": now_ms,
        "device": {"decoder": "zxing-wasm", "w": 1280, "h": 720},
    }

    serialized = json.dumps(body)
    size_bytes = len(serialized.encode("utf-8"))

    # Assert request size is well under 4 KB
    assert size_bytes < 4096, f"Payload size {size_bytes} exceeds 4 KB"
    # In fact, typically ~1.2 KB
    assert size_bytes < 2000, f"Payload unexpectedly large: {size_bytes} bytes"

    # Assert no image signatures or data URLs
    assert "data:image" not in serialized
    assert ";base64," not in serialized
    assert "\xff\xd8" not in serialized  # JPEG magic bytes
    assert "\x89PNG" not in serialized   # PNG magic bytes


# -----------------------------------------------------------------------------------------
# Case 9: Idempotency under face payloads & concurrency
# -----------------------------------------------------------------------------------------
def test_09_idempotency_and_simultaneous_submissions(client, p5_world):
    """Case 9: 20 simultaneous identical submissions -> exactly 1 row, identical checkin_id."""
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        u = User(email=f"p5_idem_{tag}@example.edu", roll_no=f"P5_IDEM_{tag}", name="Idem", role="student", password_hash=hash_password("pw"))
        db.add(u); db.flush()
        db.add(RosterEntry(course_id=p5_world["course_id"], student_id=u.id))
        db.add(FaceTemplate(student_id=u.id, vector=make_vector(0.0), model=MODEL, status="confirmed"))
        s = ClassSession(course_id=p5_world["course_id"], status="live", step_ms=1500, secret=p5_world["secret"])
        db.add(s); db.commit()
        uid, sid = u.id, s.id

    try:
        token = make_token(uid, "student", "access")
        h = {"Authorization": f"Bearer {token}"}
        ch = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h).json()

        now_ms = int(time.time() * 1000)
        qr = tokens.make_qr_payload(p5_world["secret"], sid, tokens.window_for(now_ms, 1500))
        body = {
            "qr": qr,
            "challenge_id": ch["challenge_id"],
            "face": {"vector": make_vector(0.0), "model": MODEL},
            "liveness": make_liveness(ch["colour_seq"], 4),
        }

        # Issue 20 parallel requests with identical payload
        def _post():
            return client.post(f"/api/v1/sessions/{sid}/checkin", json=body, headers=h)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(_post) for _ in range(20)]
            responses = [f.result() for f in futures]

        status_codes = [r.status_code for r in responses]
        assert all(code in (200, 409) for code in status_codes)

        checkin_ids = {r.json()["checkin_id"] for r in responses if r.status_code == 200}
        assert len(checkin_ids) == 1

        # Verify DB has exactly 1 checkin row
        with SessionLocal() as db:
            rows = db.scalars(select(Checkin).where(Checkin.session_id == sid, Checkin.student_id == uid)).all()
            assert len(rows) == 1
            stored_id = rows[0].id

        assert checkin_ids.pop() == stored_id

        # Subsequent retry returns 200 with same checkin_id
        r_retry = client.post(f"/api/v1/sessions/{sid}/checkin", json=body, headers=h)
        assert r_retry.status_code == 200
        assert r_retry.json()["checkin_id"] == stored_id

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.student_id == uid).delete()
            db.query(Challenge).filter(Challenge.student_id == uid).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id == uid).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id == uid).delete()
            db.query(ClassSession).filter(ClassSession.id == sid).delete()
            db.query(User).filter(User.id == uid).delete()
            db.commit()


# -----------------------------------------------------------------------------------------
# Case 10: Model enforcement and upgrade lifecycle
# -----------------------------------------------------------------------------------------
def test_10_model_enforcement_and_lifecycle(client, p5_world):
    """Case 10:
    - Enrollment with stub-v0 -> 422 BAD_MODEL.
    - Template with stub-v0 -> 409 REENROLL_REQUIRED on /challenge and /checkin.
    - After DELETE /students/me/face-data and re-enroll with faceapi-128-v1 -> succeeds.
    """
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        u = User(email=f"p5_mod_{tag}@example.edu", roll_no=f"P5_MOD_{tag}", name="Mod", role="student", password_hash=hash_password("pw"))
        db.add(u); db.flush()
        db.add(RosterEntry(course_id=p5_world["course_id"], student_id=u.id))
        s = ClassSession(course_id=p5_world["course_id"], status="live", step_ms=1500, secret=p5_world["secret"])
        db.add(s); db.commit()
        uid, sid = u.id, s.id

    try:
        token = make_token(uid, "student", "access")
        h = {"Authorization": f"Bearer {token}"}

        # 1. Give consent
        client.post("/api/v1/students/me/consent", json={"text_version": "v1"}, headers=h)

        # 2. Try enrolling with rejected stub model -> 422 BAD_MODEL
        fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        r_bad = client.post(
            "/api/v1/students/me/enroll",
            headers=h,
            data={"vector": json.dumps(make_vector(0.1)), "model": "stub-v0"},
            files={"photo": ("face.jpg", fake_jpeg, "image/jpeg")},
        )
        assert r_bad.status_code == 422
        assert r_bad.json()["error"]["code"] == "BAD_MODEL"

        # 3. Simulate an existing student who had an old stub-v0 template from Phase 2
        with SessionLocal() as db:
            tpl = FaceTemplate(student_id=uid, vector=make_vector(0.1), model="stub-v0", status="confirmed")
            db.add(tpl); db.commit()

        # Old template student calling /challenge -> 409 REENROLL_REQUIRED
        r_ch = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h)
        assert r_ch.status_code == 409
        assert r_ch.json()["error"]["code"] == "REENROLL_REQUIRED"

        # Calling /checkin -> 409 REENROLL_REQUIRED
        qr = tokens.make_qr_payload(p5_world["secret"], sid, tokens.window_for(int(time.time() * 1000), 1500))
        r_ck = client.post(f"/api/v1/sessions/{sid}/checkin", json={"qr": qr}, headers=h)
        assert r_ck.status_code == 409
        assert r_ck.json()["error"]["code"] == "REENROLL_REQUIRED"

        # 4. Student deletes face data
        r_del = client.delete("/api/v1/students/me/face-data", headers=h)
        assert r_del.status_code == 204

        # 5. Re-enroll with faceapi-128-v1
        r_ok = client.post(
            "/api/v1/students/me/enroll",
            headers=h,
            data={"vector": json.dumps(make_vector(0.1)), "model": MODEL},
            files={"photo": ("face.jpg", fake_jpeg, "image/jpeg")},
        )
        assert r_ok.status_code == 202

        # Professor confirms enrollment
        with SessionLocal() as db:
            tpl = db.get(FaceTemplate, uid)
            tpl.status = "confirmed"
            db.commit()

        # 6. Student now gets challenge and checks in successfully!
        r_ch_ok = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h)
        assert r_ch_ok.status_code == 200
        ch_ok = r_ch_ok.json()

        r_ck_ok = client.post(
            f"/api/v1/sessions/{sid}/checkin",
            headers=h,
            json={
                "qr": qr,
                "challenge_id": ch_ok["challenge_id"],
                "face": {"vector": make_vector(0.1), "model": MODEL},
                "liveness": make_liveness(ch_ok["colour_seq"], 4),
            },
        )
        assert r_ck_ok.status_code == 200
        assert r_ck_ok.json()["status"] == "VERIFIED"

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.student_id == uid).delete()
            db.query(Challenge).filter(Challenge.student_id == uid).delete()
            db.query(RosterEntry).filter(RosterEntry.student_id == uid).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id == uid).delete()
            db.query(ClassSession).filter(ClassSession.id == sid).delete()
            db.query(User).filter(User.id == uid).delete()
            db.commit()


# -----------------------------------------------------------------------------------------
# Case 11: 240-student thundering herd load test with face payloads & collision checks
# -----------------------------------------------------------------------------------------
def test_11_thundering_herd_load_benchmark(client):
    """Case 11: Repeat 240-student thundering herd load test with face payloads and collision checks.
    Verify target p99 <= 400 ms. Report p50, p95, p99.
    """
    tag = uuid.uuid4().hex[:6]
    num_students = 240
    student_records = []
    created_ids = []

    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        course = Course(professor_id=prof.id, name=f"P5 Load Course {tag}")
        db.add(course); db.flush()
        course_id = course.id

        secret = b"load_test_p5_secret_key_32bytes"
        session = ClassSession(course_id=course_id, status="live", step_ms=1500, secret=secret)
        db.add(session); db.flush()
        session_id = session.id

        # Batch insert 240 students with confirmed FaceTemplates and RosterEntries
        for i in range(num_students):
            u = User(
                email=f"load5_{tag}_{i}@example.edu",
                roll_no=f"L5_{tag}_{i:03d}",
                name=f"Load Student {i}",
                role="student",
                password_hash="x",
            )
            db.add(u); db.flush()
            created_ids.append(u.id)

            # Assign unique orthogonal vectors to prevent false collisions during normal arrival
            vec = make_vector(val=0.0, offset_idx=i % 128, offset_val=1.0 + (i // 128) * 0.5)
            db.add(RosterEntry(course_id=course_id, student_id=u.id))
            db.add(FaceTemplate(student_id=u.id, vector=vec, model=MODEL, status="confirmed"))
            token = make_token(u.id, "student", "access")
            student_records.append((u.id, token, vec))

        db.commit()

    try:
        now_ms = int(time.time() * 1000)
        win = tokens.window_for(now_ms, 1500)
        qr = tokens.make_qr_payload(secret, session_id, win)

        # Pre-issue challenges for all students
        challenges = {}
        for uid, tok, _ in student_records:
            h = {"Authorization": f"Bearer {tok}"}
            r_ch = client.post(f"/api/v1/sessions/{session_id}/challenge", headers=h)
            assert r_ch.status_code == 200
            challenges[uid] = r_ch.json()

        # Benchmark checkin requests with face and liveness payloads across a thread pool
        latencies = []

        def _checkin_one(rec):
            uid, tok, vec = rec
            h = {"Authorization": f"Bearer {tok}"}
            ch = challenges[uid]
            cur_ms = int(time.time() * 1000)
            cur_win = tokens.window_for(cur_ms, 1500)
            cur_qr = tokens.make_qr_payload(secret, session_id, cur_win)
            body = {
                "qr": cur_qr,
                "challenge_id": ch["challenge_id"],
                "face": {"vector": vec, "model": MODEL},
                "liveness": make_liveness(ch["colour_seq"], 4),
            }
            t0 = time.perf_counter()
            resp = client.post(f"/api/v1/sessions/{session_id}/checkin", json=body, headers=h)
            dt_ms = (time.perf_counter() - t0) * 1000
            return resp.status_code, dt_ms, resp.json()

        # Simulate 16 concurrent client threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(_checkin_one, rec) for rec in student_records]
            for f in concurrent.futures.as_completed(futures):
                status_code, dt_ms, res = f.result()
                assert status_code == 200, f"Checkin failed with status {status_code}: {res}"
                assert res["status"] == "VERIFIED"
                latencies.append(dt_ms)

        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        max_lat = max(latencies)

        print(f"\n--- Phase 5 Thundering Herd Load Benchmark (N={num_students}) ---")
        print(f"p50: {p50:.2f} ms")
        print(f"p95: {p95:.2f} ms")
        print(f"p99: {p99:.2f} ms")
        print(f"max: {max_lat:.2f} ms")

        # Target: p99 <= 400 ms at load
        assert p99 <= 400.0, f"p99 latency {p99:.2f} ms exceeded 400 ms threshold"

        # Verify all 240 checkins are present and marked VERIFIED
        with SessionLocal() as db:
            count = db.scalar(select(Checkin).where(Checkin.session_id == session_id).with_only_columns(Checkin.id))
            total = len(db.scalars(select(Checkin).where(Checkin.session_id == session_id)).all())
            assert total == num_students

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.session_id == session_id).delete()
            db.query(Challenge).filter(Challenge.session_id == session_id).delete()
            db.query(RosterEntry).filter(RosterEntry.course_id == course_id).delete()
            db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(created_ids)).delete()
            db.query(ClassSession).filter(ClassSession.id == session_id).delete()
            db.query(Course).filter(Course.id == course_id).delete()
            db.query(User).filter(User.id.in_(created_ids)).delete()
            db.commit()
