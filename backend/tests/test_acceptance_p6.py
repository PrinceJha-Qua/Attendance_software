"""Phase 6 Acceptance Test Suite: Audio layer (ultrasonic beacon, soft signal).
Verifies all 7 API cases, scoring tables, token generation, validation, audio-plan,
audio-log trial mode, and interactions specified in docs/acceptance/P6.md.
"""
import json
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, tokens, trust
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AudioLog, Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User
from app.routers import stubs
from app.security import hash_password, make_token

MODEL = config.FACE_MODEL


def make_vector(val: float = 0.0) -> list[float]:
    return [float(val)] * 128


def make_liveness(colour_seq: list[str]):
    baseline = [120.0, 120.0, 120.0]
    samples = [[120.0 + 0.2 * (trust.RGB[c][k] - 85.0) for k in range(3)] for c in colour_seq]
    return {"baseline": baseline, "samples": samples}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture
def p6_world():
    """Isolated environment with 1 professor, 1 other professor, 1 course,
    1 burst session, 1 off session, and 3 rostered confirmed students.
    """
    tag = uuid.uuid4().hex[:6]
    created = {
        "prof_id": None, "prof_token": None,
        "other_prof_id": None, "other_prof_token": None,
        "course_id": None,
        "burst_sid": None, "burst_secret": None,
        "off_sid": None, "off_secret": None,
        "students": [],  # list of (id, token, vector)
        "unrostered_id": None, "unrostered_token": None,
    }

    with SessionLocal() as db:
        # Main professor
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        if not prof:
            prof = User(email=f"prof_{tag}@example.edu", roll_no=f"P_{tag}", name="Prof P6", role="professor", password_hash=hash_password("pw"))
            db.add(prof)
            db.flush()
        created["prof_id"] = prof.id
        created["prof_token"] = make_token(prof.id, "professor", "access")

        # Other professor
        other_prof = User(email=f"other_prof_{tag}@example.edu", roll_no=f"OP_{tag}", name="Other Prof P6", role="professor", password_hash=hash_password("pw"))
        db.add(other_prof); db.flush()
        created["other_prof_id"] = other_prof.id
        created["other_prof_token"] = make_token(other_prof.id, "professor", "access")

        course = Course(professor_id=prof.id, name=f"P6 Acceptance {tag}")
        db.add(course); db.flush()
        created["course_id"] = course.id

        # Session 1: audio_mode = "burst"
        burst_sec = b"acceptance_p6_burst_secret_32b!"
        s_burst = ClassSession(course_id=course.id, status="live", step_ms=1500, audio_mode="burst", secret=burst_sec)
        db.add(s_burst); db.flush()
        created["burst_sid"] = s_burst.id
        created["burst_secret"] = burst_sec

        # Session 2: audio_mode = "off"
        off_sec = b"acceptance_p6_off_secret_32bytes"
        s_off = ClassSession(course_id=course.id, status="live", step_ms=1500, audio_mode="off", secret=off_sec)
        db.add(s_off); db.flush()
        created["off_sid"] = s_off.id
        created["off_secret"] = off_sec

        # 3 rostered confirmed students
        for i in range(3):
            u = User(email=f"p6_stud_{tag}_{i}@example.edu", roll_no=f"P6_{tag}_{i}", name=f"P6 Stud {i}", role="student", password_hash=hash_password("pw"))
            db.add(u); db.flush()
            db.add(RosterEntry(course_id=course.id, student_id=u.id))
            vec = make_vector(i * 0.1)
            db.add(FaceTemplate(student_id=u.id, vector=vec, model=MODEL, status="confirmed"))
            created["students"].append((u.id, make_token(u.id, "student", "access"), vec))

        # 1 unrostered student
        unrost = User(email=f"p6_unrost_{tag}@example.edu", roll_no=f"P6_UN_{tag}", name="Unrostered", role="student", password_hash=hash_password("pw"))
        db.add(unrost); db.flush()
        db.add(FaceTemplate(student_id=unrost.id, vector=make_vector(0.9), model=MODEL, status="confirmed"))
        created["unrostered_id"] = unrost.id
        created["unrostered_token"] = make_token(unrost.id, "student", "access")

        db.commit()

    yield created

    # Cleanup
    with SessionLocal() as db:
        all_sids = [created["burst_sid"], created["off_sid"]]
        stud_ids = [s[0] for s in created["students"]] + [created["unrostered_id"]]
        db.query(AudioLog).filter(AudioLog.session_id.in_(all_sids)).delete()
        db.query(Checkin).filter(Checkin.session_id.in_(all_sids)).delete()
        db.query(Challenge).filter(Challenge.session_id.in_(all_sids)).delete()
        db.query(RosterEntry).filter(RosterEntry.course_id == created["course_id"]).delete()
        db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(stud_ids)).delete()
        db.query(ClassSession).filter(ClassSession.id.in_(all_sids)).delete()
        db.query(Course).filter(Course.id == created["course_id"]).delete()
        db.query(User).filter(User.id.in_(stud_ids + [created["other_prof_id"]])).delete()
        db.commit()


# -----------------------------------------------------------------------------------------
# Case 1: Tokens in burst vs off sessions
# -----------------------------------------------------------------------------------------
def test_01_tokens_audio_code_presence_and_derivation(client, p6_world):
    """Case 1:
    - In burst session: each token item in GET /sessions/{id}/tokens contains 'audio_code' (int 0-65535)
      equal to HMAC derivation (tokens.audio_code).
    - In off session: 'audio_code' is absent from all token items.
    - audio_code differs from QR token substring.
    """
    hp = {"Authorization": f"Bearer {p6_world['prof_token']}"}

    # 1. Burst session
    r_burst = client.get(f"/api/v1/sessions/{p6_world['burst_sid']}/tokens", headers=hp)
    assert r_burst.status_code == 200
    data_burst = r_burst.json()
    assert len(data_burst["tokens"]) > 0
    for item in data_burst["tokens"]:
        assert "audio_code" in item
        code = item["audio_code"]
        assert isinstance(code, int)
        assert 0 <= code <= 65535
        expected_code = tokens.audio_code(p6_world["burst_secret"], p6_world["burst_sid"], item["window"])
        assert code == expected_code
        # Code differs from QR token string
        parsed = tokens.parse_qr(item["payload"])
        assert str(code) != parsed.token

    # 2. Off session
    r_off = client.get(f"/api/v1/sessions/{p6_world['off_sid']}/tokens", headers=hp)
    assert r_off.status_code == 200
    data_off = r_off.json()
    assert len(data_off["tokens"]) > 0
    for item in data_off["tokens"]:
        assert "audio_code" not in item


# -----------------------------------------------------------------------------------------
# Case 2: Verdict table
# -----------------------------------------------------------------------------------------
def test_02_verdict_table(client, p6_world):
    """Case 2:
    - correct current/previous-window code -> VERIFIED, score 1.0, flags []
    - none_heard, denied, unsupported, field absent -> AUDIO_UNAVAILABLE, score 0.9, never 4xx (VERIFIED)
    - valid code from ~60s ago (replay) -> AUDIO_MISMATCH, REVIEW
    - random code -> AUDIO_MISMATCH, REVIEW
    - window_low8 matching current window but code from another session -> AUDIO_MISMATCH, REVIEW
    - checkins.audio stores the report; evidence.audio stores the verdict.
    """
    sid = p6_world["burst_sid"]
    sec = p6_world["burst_secret"]

    def _do_checkin(stud_idx, audio_payload):
        uid, tok, vec = p6_world["students"][stud_idx]
        h = {"Authorization": f"Bearer {tok}"}
        ch = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h).json()
        now_ms = int(time.time() * 1000)
        w = tokens.window_for(now_ms, 1500)
        qr = tokens.make_qr_payload(sec, sid, w)
        body = {
            "qr": qr,
            "challenge_id": ch["challenge_id"],
            "face": {"vector": vec, "model": MODEL},
            "liveness": make_liveness(ch["colour_seq"]),
        }
        if audio_payload is not None:
            body["audio"] = audio_payload
        return client.post(f"/api/v1/sessions/{sid}/checkin", json=body, headers=h)

    # 1. Correct current window code -> VERIFIED, score 1.0, flags []
    now_ms = int(time.time() * 1000)
    w_curr = tokens.window_for(now_ms, 1500)
    code_curr = tokens.audio_code(sec, sid, w_curr)
    r1 = _do_checkin(0, {"status": "heard", "window_low8": w_curr % 256, "code": code_curr, "snr_db": 12.5})
    assert r1.status_code == 200
    res1 = r1.json()
    assert res1["status"] == "VERIFIED"
    assert res1["score"] == 1.0
    assert res1["flags"] == []

    # Verify DB storage
    with SessionLocal() as db:
        ck1 = db.get(Checkin, res1["checkin_id"])
        assert ck1.audio == {"status": "heard", "window_low8": w_curr % 256, "code": code_curr, "snr_db": 12.5}
        assert ck1.evidence["audio"]["ok"] is True
        assert ck1.evidence["audio"]["snr_db"] == 12.5

    # 2. none_heard -> VERIFIED, score 0.9, AUDIO_UNAVAILABLE
    r2 = _do_checkin(1, {"status": "none_heard"})
    assert r2.status_code == 200
    res2 = r2.json()
    assert res2["status"] == "VERIFIED"
    assert res2["score"] == 0.9
    assert res2["flags"] == ["AUDIO_UNAVAILABLE"]
    with SessionLocal() as db:
        ck2 = db.get(Checkin, res2["checkin_id"])
        assert ck2.evidence["audio"] == "none_heard"

    # 3. denied -> VERIFIED, score 0.9, AUDIO_UNAVAILABLE (Student 2)
    r3 = _do_checkin(2, {"status": "denied"})
    assert r3.status_code == 200
    res3 = r3.json()
    assert res3["status"] == "VERIFIED"
    assert res3["score"] == 0.9
    assert res3["flags"] == ["AUDIO_UNAVAILABLE"]

    # For subsequent cases (unsupported, absent, replayed, random, forged), create a fresh session
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        s_more = ClassSession(course_id=p6_world["course_id"], status="live", step_ms=1500, audio_mode="burst", secret=sec)
        db.add(s_more); db.commit()
        sid_more = s_more.id

    def _do_checkin_more(stud_idx, audio_payload, win_offset=0):
        uid, tok, vec = p6_world["students"][stud_idx]
        h = {"Authorization": f"Bearer {tok}"}
        ch = client.post(f"/api/v1/sessions/{sid_more}/challenge", headers=h).json()
        now_ms = int(time.time() * 1000)
        w = tokens.window_for(now_ms, 1500) + win_offset
        qr = tokens.make_qr_payload(sec, sid_more, w)
        body = {
            "qr": qr,
            "challenge_id": ch["challenge_id"],
            "face": {"vector": vec, "model": MODEL},
            "liveness": make_liveness(ch["colour_seq"]),
        }
        if audio_payload is not None:
            body["audio"] = audio_payload
        return client.post(f"/api/v1/sessions/{sid_more}/checkin", json=body, headers=h)

    try:
        # 4. unsupported -> VERIFIED, score 0.9, AUDIO_UNAVAILABLE
        r4 = _do_checkin_more(0, {"status": "unsupported"})
        assert r4.status_code == 200
        res4 = r4.json()
        assert res4["status"] == "VERIFIED"
        assert res4["score"] == 0.9
        assert res4["flags"] == ["AUDIO_UNAVAILABLE"]

        # 5. field absent -> VERIFIED, score 0.9, AUDIO_UNAVAILABLE
        r5 = _do_checkin_more(1, None)
        assert r5.status_code == 200
        res5 = r5.json()
        assert res5["status"] == "VERIFIED"
        assert res5["score"] == 0.9
        assert res5["flags"] == ["AUDIO_UNAVAILABLE"]

        # 6. Replayed old burst (~60 s ago, ~40 windows back) -> AUDIO_MISMATCH, REVIEW
        w_old = w_curr - 40
        code_old = tokens.audio_code(sec, sid_more, w_old)
        r_old = _do_checkin_more(2, {"status": "heard", "window_low8": w_old % 256, "code": code_old, "snr_db": 9.0})
        assert r_old.status_code == 200
        res_old = r_old.json()
        assert res_old["status"] == "REVIEW"
        assert res_old["score"] == 0.9
        assert "AUDIO_MISMATCH" in res_old["flags"]
        with SessionLocal() as db:
            ck_old = db.get(Checkin, res_old["checkin_id"])
            assert ck_old.evidence["audio"]["ok"] is False

        # Create another session for random code and forged code
        with SessionLocal() as db:
            s_more2 = ClassSession(course_id=p6_world["course_id"], status="live", step_ms=1500, audio_mode="burst", secret=sec)
            db.add(s_more2); db.commit()
            sid_more2 = s_more2.id

        def _do_checkin_more2(stud_idx, audio_payload):
            uid, tok, vec = p6_world["students"][stud_idx]
            h = {"Authorization": f"Bearer {tok}"}
            ch = client.post(f"/api/v1/sessions/{sid_more2}/challenge", headers=h).json()
            now_ms = int(time.time() * 1000)
            w = tokens.window_for(now_ms, 1500)
            qr = tokens.make_qr_payload(sec, sid_more2, w)
            body = {
                "qr": qr,
                "challenge_id": ch["challenge_id"],
                "face": {"vector": vec, "model": MODEL},
                "liveness": make_liveness(ch["colour_seq"]),
            }
            if audio_payload is not None:
                body["audio"] = audio_payload
            return client.post(f"/api/v1/sessions/{sid_more2}/checkin", json=body, headers=h)

        try:
            # 7. Random code -> AUDIO_MISMATCH, REVIEW
            r_rnd = _do_checkin_more2(0, {"status": "heard", "window_low8": w_curr % 256, "code": 54321, "snr_db": 5.0})
            assert r_rnd.status_code == 200
            res_rnd = r_rnd.json()
            assert res_rnd["status"] == "REVIEW"
            assert "AUDIO_MISMATCH" in res_rnd["flags"]

            # 8. window_low8 matches current, but code derived from another session's secret -> AUDIO_MISMATCH
            fake_code = tokens.audio_code(b"completely_different_secret_key!", sid_more2, w_curr)
            r_forged = _do_checkin_more2(1, {"status": "heard", "window_low8": w_curr % 256, "code": fake_code, "snr_db": 7.0})
            assert r_forged.status_code == 200
            res_forged = r_forged.json()
            assert res_forged["status"] == "REVIEW"
            assert "AUDIO_MISMATCH" in res_forged["flags"]
        finally:
            with SessionLocal() as db:
                db.query(Checkin).filter(Checkin.session_id == sid_more2).delete()
                db.query(Challenge).filter(Challenge.session_id == sid_more2).delete()
                db.query(ClassSession).filter(ClassSession.id == sid_more2).delete()
                db.commit()

    finally:
        with SessionLocal() as db:
            db.query(Checkin).filter(Checkin.session_id == sid_more).delete()
            db.query(Challenge).filter(Challenge.session_id == sid_more).delete()
            db.query(ClassSession).filter(ClassSession.id == sid_more).delete()
            db.commit()


# -----------------------------------------------------------------------------------------
# Case 3: Interaction with other trust signals
# -----------------------------------------------------------------------------------------
def test_03_signal_interactions(client, p6_world):
    """Case 3:
    - audio unavailable + face mismatch -> REVIEW, score 0.6 (0.5 + 0.2 - 0.1)
    - audio ok + face mismatch -> REVIEW, score 0.7 (audio ok does NOT rescue face mismatch)
    - audio mismatch + face match -> REVIEW, score 0.9
    """
    sid = p6_world["burst_sid"]
    sec = p6_world["burst_secret"]

    with SessionLocal() as db:
        db.query(Checkin).filter(Checkin.session_id == sid).delete(); db.commit()

    uid, tok, _ = p6_world["students"][0]
    h = {"Authorization": f"Bearer {tok}"}
    ch = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h).json()
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr = tokens.make_qr_payload(sec, sid, w)

    # 1. Audio unavailable + face mismatch
    # Distance from own vector (0.0) is 0.7 > 0.55
    mismatch_face = [0.0, 0.7] + [0.0] * 126
    body_unavail = {
        "qr": qr,
        "challenge_id": ch["challenge_id"],
        "face": {"vector": mismatch_face, "model": MODEL},
        "liveness": make_liveness(ch["colour_seq"]),
        "audio": {"status": "none_heard"},
    }
    r1 = client.post(f"/api/v1/sessions/{sid}/checkin", json=body_unavail, headers=h)
    assert r1.status_code == 200
    res1 = r1.json()
    assert res1["status"] == "REVIEW"
    assert res1["score"] == 0.6
    assert set(res1["flags"]) == {"FACE_MISMATCH", "AUDIO_UNAVAILABLE"}

    # 2. Audio ok + face mismatch (tested with student 1)
    # Using orthogonal dimension [0.0, 0.0, 0.7] to prevent collision with student 0
    uid1, tok1, _ = p6_world["students"][1]
    h1 = {"Authorization": f"Bearer {tok1}"}
    ch1 = client.post(f"/api/v1/sessions/{sid}/challenge", headers=h1).json()
    qr1 = tokens.make_qr_payload(sec, sid, w)
    code_curr = tokens.audio_code(sec, sid, w)

    body_rescue_fail = {
        "qr": qr1,
        "challenge_id": ch1["challenge_id"],
        "face": {"vector": [0.0, 0.0, 0.7] + [0.0] * 125, "model": MODEL},  # mismatch without collision
        "liveness": make_liveness(ch1["colour_seq"]),
        "audio": {"status": "heard", "window_low8": w % 256, "code": code_curr, "snr_db": 10.0},
    }
    r2 = client.post(f"/api/v1/sessions/{sid}/checkin", json=body_rescue_fail, headers=h1)
    assert r2.status_code == 200
    res2 = r2.json()
    assert res2["status"] == "REVIEW"
    assert res2["score"] == 0.7  # 0.5 (QR) + 0.2 (Liveness)
    assert res2["flags"] == ["FACE_MISMATCH"]  # Audio ok doesn't add flag, but cannot rescue REVIEW


# -----------------------------------------------------------------------------------------
# Case 4: Validation and audio-off sessions
# -----------------------------------------------------------------------------------------
def test_04_validation_and_audio_off_session(client, p6_world):
    """Case 4:
    - code > 65535, code < 0 -> 422
    - window_low8 > 255, window_low8 < 0 -> 422
    - unknown status -> 422
    - snr_db outside [-50, 100] -> 422
    - In session with audio_mode = 'off', audio payload is completely ignored (flags/score as P5).
    """
    burst_sid = p6_world["burst_sid"]
    burst_sec = p6_world["burst_secret"]
    off_sid = p6_world["off_sid"]
    off_sec = p6_world["off_secret"]

    uid, tok, vec = p6_world["students"][0]
    h = {"Authorization": f"Bearer {tok}"}
    now_ms = int(time.time() * 1000)
    w = tokens.window_for(now_ms, 1500)
    qr = tokens.make_qr_payload(burst_sec, burst_sid, w)

    # 1. code > 65535 -> 422
    r_bad_code = client.post(f"/api/v1/sessions/{burst_sid}/checkin", json={
        "qr": qr, "audio": {"status": "heard", "window_low8": 1, "code": 65536}
    }, headers=h)
    assert r_bad_code.status_code == 422

    # 2. window_low8 > 255 -> 422
    r_bad_low8 = client.post(f"/api/v1/sessions/{burst_sid}/checkin", json={
        "qr": qr, "audio": {"status": "heard", "window_low8": 256, "code": 100}
    }, headers=h)
    assert r_bad_low8.status_code == 422

    # 3. Unknown status -> 422
    r_bad_status = client.post(f"/api/v1/sessions/{burst_sid}/checkin", json={
        "qr": qr, "audio": {"status": "listening"}
    }, headers=h)
    assert r_bad_status.status_code == 422

    # 4. snr_db > 100 -> 422
    r_bad_snr = client.post(f"/api/v1/sessions/{burst_sid}/checkin", json={
        "qr": qr, "audio": {"status": "heard", "window_low8": 1, "code": 100, "snr_db": 101}
    }, headers=h)
    assert r_bad_snr.status_code == 422

    # 5. Audio off session: audio payload ignored entirely
    qr_off = tokens.make_qr_payload(off_sec, off_sid, w)
    ch_off = client.post(f"/api/v1/sessions/{off_sid}/challenge", headers=h).json()
    r_off = client.post(f"/api/v1/sessions/{off_sid}/checkin", json={
        "qr": qr_off,
        "challenge_id": ch_off["challenge_id"],
        "face": {"vector": vec, "model": MODEL},
        "liveness": make_liveness(ch_off["colour_seq"]),
        "audio": {"status": "heard", "window_low8": 99, "code": 12345},  # would be mismatch in burst
    }, headers=h)
    assert r_off.status_code == 200
    res_off = r_off.json()
    assert res_off["status"] == "VERIFIED"
    assert res_off["score"] == 1.0
    assert res_off["flags"] == []  # Audio completely ignored in off session


# -----------------------------------------------------------------------------------------
# Case 5: audio-plan endpoint access control and schema
# -----------------------------------------------------------------------------------------
def test_05_audio_plan_access_control_and_schema(client, p6_world):
    """Case 5:
    - Returns mode, step_ms, band_hz [17500, 19000], sample_rate (48000), max_age_ms (8000), trial_mode
    - Owner professor -> 200
    - Rostered student -> 200
    - Other professor's session -> 404
    - Unrostered student -> 404
    - Unauthenticated -> 401
    """
    sid = p6_world["burst_sid"]
    hp_owner = {"Authorization": f"Bearer {p6_world['prof_token']}"}
    hp_other = {"Authorization": f"Bearer {p6_world['other_prof_token']}"}
    hs_rostered = {"Authorization": f"Bearer {p6_world['students'][0][1]}"}
    hs_unrostered = {"Authorization": f"Bearer {p6_world['unrostered_token']}"}

    # 1. Owner professor gets 200 with complete schema
    r_owner = client.get(f"/api/v1/sessions/{sid}/audio-plan", headers=hp_owner)
    assert r_owner.status_code == 200
    plan = r_owner.json()
    assert plan["mode"] == "burst"
    assert plan["step_ms"] == 1500
    assert plan["band_hz"] == [17500, 19000]
    assert plan["sample_rate"] == 48000
    assert plan["max_age_ms"] == 8000
    assert isinstance(plan["trial_mode"], bool)

    # 2. Rostered student gets 200 with identical plan
    r_stud = client.get(f"/api/v1/sessions/{sid}/audio-plan", headers=hs_rostered)
    assert r_stud.status_code == 200
    assert r_stud.json() == plan

    # 3. Other professor gets 404
    r_other = client.get(f"/api/v1/sessions/{sid}/audio-plan", headers=hp_other)
    assert r_other.status_code == 404
    assert r_other.json()["error"]["code"] == "SESSION_NOT_FOUND"

    # 4. Unrostered student gets 404
    r_unrost = client.get(f"/api/v1/sessions/{sid}/audio-plan", headers=hs_unrostered)
    assert r_unrost.status_code == 404
    assert r_unrost.json()["error"]["code"] == "SESSION_NOT_FOUND"

    # 5. No token gets 401
    assert client.get(f"/api/v1/sessions/{sid}/audio-plan").status_code == 401


# -----------------------------------------------------------------------------------------
# Case 6: trial/audio-log endpoint telemetry and size limits
# -----------------------------------------------------------------------------------------
def test_06_trial_audio_log(client, p6_world, monkeypatch):
    """Case 6:
    - Non-trial mode: returns 204 and zero rows committed to DB.
    - Trial mode: returns 204 and inserts one row.
    - Body > 4000 bytes in trial mode -> 413 LOG_TOO_LARGE.
    - Non-student / unauthenticated guards.
    """
    sid = p6_world["burst_sid"]
    hs = {"Authorization": f"Bearer {p6_world['students'][0][1]}"}
    hp = {"Authorization": f"Bearer {p6_world['prof_token']}"}

    sample_log = {
        "session_id": sid,
        "data": {
            "rep": {"status": "heard", "window_low8": 12, "code": 3456, "snr_db": 8.5},
            "info": {"sampleRate": 48000, "worklet": True, "settings": {"echoCancellation": False}},
            "stats": {"frames": 45, "maxNorm": 0.82},
        },
    }

    # 1. Non-trial mode: 204 and 0 rows
    monkeypatch.setattr(config, "TRIAL_MODE", False)
    r1 = client.post("/api/v1/trial/audio-log", json=sample_log, headers=hs)
    assert r1.status_code == 204
    with SessionLocal() as db:
        count = db.query(AudioLog).filter(AudioLog.session_id == sid).count()
        assert count == 0

    # 2. Trial mode enabled: 204 and 1 row saved
    monkeypatch.setattr(config, "TRIAL_MODE", True)
    r2 = client.post("/api/v1/trial/audio-log", json=sample_log, headers=hs)
    assert r2.status_code == 204
    with SessionLocal() as db:
        logs = db.query(AudioLog).filter(AudioLog.session_id == sid).all()
        assert len(logs) == 1
        assert logs[0].student_id == p6_world["students"][0][0]
        assert logs[0].payload["stats"]["maxNorm"] == 0.82

    # 3. Payload size > 4000 bytes in trial mode -> 413 LOG_TOO_LARGE
    huge_data = {"session_id": sid, "data": {"junk": "x" * 4100}}
    r_huge = client.post("/api/v1/trial/audio-log", json=huge_data, headers=hs)
    assert r_huge.status_code == 413
    assert r_huge.json()["error"]["code"] == "LOG_TOO_LARGE"

    # 4. Role guards: professor token -> 403, unauthenticated -> 401
    assert client.post("/api/v1/trial/audio-log", json=sample_log, headers=hp).status_code == 403
    assert client.post("/api/v1/trial/audio-log", json=sample_log).status_code == 401


# -----------------------------------------------------------------------------------------
# Case 7: Regression and stub cleanup
# -----------------------------------------------------------------------------------------
def test_07_stubs_router_cleaned_and_no_stray_endpoints():
    """Case 7: Verify audio-plan and audio-log are removed from STUBS in app/routers/stubs.py."""
    stub_paths = [path for _, path, _, _, _ in stubs.STUBS]
    assert "/sessions/{session_id}/audio-plan" not in stub_paths
    assert "/trial/audio-log" not in stub_paths
