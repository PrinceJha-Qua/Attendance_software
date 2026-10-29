"""Phase 5 checks written by the builder (AGY owns the full acceptance suite). Own users/course, cleaned up after."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, tokens, trust
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, User
from app.security import hash_password, make_token

M = config.FACE_MODEL


def vec(seed: float, bump: float = 0.0):
    return [seed + (bump if i == 0 else 0.0) for i in range(128)]


def test_trust_scorer_table():
    assert trust.score(face_dist=0.3, live_ok=True, collision=False) == ("VERIFIED", 1.0, [])
    assert trust.score(face_dist=0.9, live_ok=True, collision=False)[0] == "REVIEW"
    assert trust.score(face_dist=0.3, live_ok=False, collision=False)[2] == ["LIVENESS_FAIL"]
    assert trust.score(face_dist=0.3, live_ok=True, collision=True)[2] == ["FACE_COLLISION"]
    assert trust.score(face_dist=None, live_ok=None, collision=False)[0] == "REVIEW"


def test_liveness_math():
    seq = ["red", "green", "blue", "yellow"]
    base = [120, 120, 120]
    good = [[120 + 0.2 * (trust.RGB[c][k] - 85) for k in range(3)] for c in seq]  # face drifts toward each colour
    assert trust.liveness(seq, base, good)[0]
    assert not trust.liveness(seq, base, [base] * 4)[0]                              # no reaction (a photo or a screen replay)
    assert not trust.liveness(seq, base, [[130, 130, 130]] * 4)[0]                   # brighter but not coloured
    assert not trust.liveness(seq, base, good[:3])[0]


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        course = Course(professor_id=prof.id, name=f"P5 {tag}")
        db.add(course); db.flush()
        studs = []
        for n, v in enumerate([vec(0.10), vec(0.30), vec(0.50)]):
            u = User(email=f"p5_{tag}_{n}@example.edu", roll_no=f"P5{tag}{n}", name=f"P5 Student {n}", role="student", password_hash=hash_password("x"))
            db.add(u); db.flush()
            db.add(FaceTemplate(student_id=u.id, vector=v, model=M, status="confirmed"))
            db.add(RosterEntry(course_id=course.id, student_id=u.id))
            studs.append(u.id)
        s = ClassSession(course_id=course.id, status="live", step_ms=1500, secret=b"k" * 32)
        db.add(s); db.commit()
        out = dict(course=course.id, sid=s.id, secret=s.secret, studs=studs, prof=make_token(prof.id, "professor", "access"))
    yield out
    with SessionLocal() as db:
        db.query(Checkin).filter(Checkin.session_id == out["sid"]).delete()
        db.query(Challenge).filter(Challenge.session_id == out["sid"]).delete()
        db.query(RosterEntry).filter(RosterEntry.course_id == out["course"]).delete()
        db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(out["studs"])).delete()
        db.query(ClassSession).filter(ClassSession.id == out["sid"]).delete()
        db.query(Course).filter(Course.id == out["course"]).delete()
        db.query(User).filter(User.id.in_(out["studs"])).delete()
        db.commit()


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


def go(client, w, i, face, live="good"):
    h = {"Authorization": f"Bearer {make_token(w['studs'][i], 'student', 'access')}"}
    ch = client.post(f"/api/v1/sessions/{w['sid']}/challenge", headers=h).json()
    body = {"qr": tokens.make_qr_payload(w["secret"], w["sid"], tokens.window_for(int(time.time() * 1000), 1500)), "challenge_id": ch["challenge_id"]}
    if face is not None:
        body["face"] = {"vector": face, "model": M}
    if live == "good":
        base = [120, 120, 120]
        body["liveness"] = {"baseline": base, "samples": [[120 + 0.2 * (trust.RGB[c][k] - 85) for k in range(3)] for c in ch["colour_seq"]]}
    elif live == "flat":
        body["liveness"] = {"baseline": [120] * 3, "samples": [[120] * 3] * 4}
    r = client.post(f"/api/v1/sessions/{w['sid']}/checkin", json=body, headers=h)
    return r, h


def test_right_face_verified(client, world):
    r, _ = go(client, world, 0, vec(0.10, 0.02))
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED" and r.json()["score"] == 1.0 and r.json()["flags"] == []


def test_wrong_face_review_with_lookalike(client, world):
    r, _ = go(client, world, 0, vec(0.30, 0.01))  # student 0 shows student 1's face
    assert r.json()["status"] == "REVIEW" and "FACE_MISMATCH" in r.json()["flags"]
    with SessionLocal() as db:
        ev = db.scalar(select(Checkin.evidence).where(Checkin.session_id == world["sid"]))
        assert ev["looks_like"]["student_id"] == world["studs"][1]


def test_same_face_two_phones_both_review(client, world):
    a, ha = go(client, world, 0, vec(0.10))
    b, hb = go(client, world, 1, vec(0.10, 0.01))  # student 1 presents student 0's face
    assert a.json()["status"] == "VERIFIED" and "FACE_COLLISION" in b.json()["flags"] and b.json()["status"] == "REVIEW"
    assert client.get(f"/api/v1/sessions/{world['sid']}/checkin/me", headers=ha).json()["status"] == "REVIEW"  # the first one is pulled in too


def test_liveness_fail_and_missing(client, world):
    r, _ = go(client, world, 0, vec(0.10), live="flat")
    assert r.json()["status"] == "REVIEW" and r.json()["flags"] == ["LIVENESS_FAIL"]
    r, _ = go(client, world, 1, vec(0.30), live=None)
    assert r.json()["flags"] == ["LIVENESS_MISSING"]


def test_no_face_is_review_not_reject(client, world):
    r, _ = go(client, world, 2, None, live=None)
    assert r.status_code == 200 and r.json()["status"] == "REVIEW" and set(r.json()["flags"]) == {"FACE_MISSING", "LIVENESS_MISSING"}


def test_challenge_is_single_use_and_bound(client, world):
    r, h = go(client, world, 0, vec(0.10))
    assert r.json()["status"] == "VERIFIED"
    with SessionLocal() as db:
        assert db.scalar(select(Challenge.used).where(Challenge.student_id == world["studs"][0]))
    # another student replaying the first student's challenge id gets LIVENESS_FAIL
    with SessionLocal() as db:
        cid = db.scalar(select(Challenge.id).where(Challenge.student_id == world["studs"][0]))
    h1 = {"Authorization": f"Bearer {make_token(world['studs'][1], 'student', 'access')}"}
    qr = tokens.make_qr_payload(world["secret"], world["sid"], tokens.window_for(int(time.time() * 1000), 1500))
    r = client.post(f"/api/v1/sessions/{world['sid']}/checkin", headers=h1, json={"qr": qr, "challenge_id": cid, "face": {"vector": vec(0.30), "model": M},
                    "liveness": {"baseline": [1, 1, 1], "samples": [[1, 1, 1]] * 4}})
    assert "LIVENESS_FAIL" in r.json()["flags"]


def test_bad_payloads_and_models(client, world):
    h = {"Authorization": f"Bearer {make_token(world['studs'][0], 'student', 'access')}"}
    qr = tokens.make_qr_payload(world["secret"], world["sid"], tokens.window_for(int(time.time() * 1000), 1500))
    assert client.post(f"/api/v1/sessions/{world['sid']}/checkin", headers=h, json={"qr": qr, "face": {"vector": [0.1] * 127, "model": M}}).status_code == 422
    inf = '{"qr": "%s", "face": {"vector": [Infinity%s], "model": "%s"}}' % (qr, ", 0.1" * 127, M)
    assert client.post(f"/api/v1/sessions/{world['sid']}/checkin", headers={**h, "content-type": "application/json"}, content=inf).status_code == 422
    assert client.post(f"/api/v1/sessions/{world['sid']}/checkin", headers=h, json={"qr": qr, "liveness": {"baseline": [0, 0, 999], "samples": [[0, 0, 0]]}}).status_code == 422
    # wrong model in the request: counted as no face evidence
    r = client.post(f"/api/v1/sessions/{world['sid']}/checkin", headers=h, json={"qr": qr, "face": {"vector": vec(0.10), "model": "stub-v0"}})
    assert "FACE_MISSING" in r.json()["flags"]


def test_old_template_model_must_reenroll(client, world):
    with SessionLocal() as db:
        db.get(FaceTemplate, world["studs"][0]).model = "stub-v0"; db.commit()
    h = {"Authorization": f"Bearer {make_token(world['studs'][0], 'student', 'access')}"}
    r = client.post(f"/api/v1/sessions/{world['sid']}/challenge", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REENROLL_REQUIRED"


def test_stub_model_cannot_enroll(client):
    with SessionLocal() as db:
        u = User(email=f"p5e_{uuid.uuid4().hex[:6]}@example.edu", roll_no=uuid.uuid4().hex[:8], name="E", role="student", password_hash="x")
        db.add(u); db.commit(); uid = u.id
    try:
        h = {"Authorization": f"Bearer {make_token(uid, 'student', 'access')}"}
        r = client.post("/api/v1/students/me/enroll", headers=h, data={"vector": "[0.1]", "model": "stub-v0"}, files={"photo": ("a.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")})
        assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_MODEL"
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == uid).delete(); db.commit()


# ---- phase 6: audio verdicts ----
def _burst(world):
    with SessionLocal() as db:
        db.get(ClassSession, world["sid"]).audio_mode = "burst"; db.commit()


def _audio_go(client, world, i, audio):
    h = {"Authorization": f"Bearer {make_token(world['studs'][i], 'student', 'access')}"}
    ch = client.post(f"/api/v1/sessions/{world['sid']}/challenge", headers=h).json()
    base = [120, 120, 120]
    body = {"qr": tokens.make_qr_payload(world["secret"], world["sid"], tokens.window_for(int(time.time() * 1000), 1500)), "challenge_id": ch["challenge_id"],
            "face": {"vector": vec([0.10, 0.30, 0.50][i]), "model": M},
            "liveness": {"baseline": base, "samples": [[120 + 0.2 * (trust.RGB[c][k] - 85) for k in range(3)] for c in ch["colour_seq"]]}}
    if audio is not None:
        body["audio"] = audio
    return client.post(f"/api/v1/sessions/{world['sid']}/checkin", json=body, headers=h).json()


def test_audio_ok_unavailable_mismatch(client, world):
    _burst(world)
    w = tokens.window_for(int(time.time() * 1000), 1500) - 1
    good = {"status": "heard", "window_low8": w % 256, "code": tokens.audio_code(world["secret"], world["sid"], w), "snr_db": 8}
    r = _audio_go(client, world, 0, good)
    assert r["status"] == "VERIFIED" and r["score"] == 1.0 and r["flags"] == []
    r = _audio_go(client, world, 1, {"status": "none_heard"})
    assert r["status"] == "VERIFIED" and r["score"] == 0.9 and r["flags"] == ["AUDIO_UNAVAILABLE"]       # never a rejection
    r = _audio_go(client, world, 2, None)
    assert r["flags"] == ["AUDIO_UNAVAILABLE"]


def test_audio_replayed_old_burst_and_wrong_code(client, world):
    _burst(world)
    old = tokens.window_for(int(time.time() * 1000), 1500) - 40  # about 60 s old
    r = _audio_go(client, world, 0, {"status": "heard", "window_low8": old % 256, "code": tokens.audio_code(world["secret"], world["sid"], old)})
    assert r["status"] == "REVIEW" and "AUDIO_MISMATCH" in r["flags"]
    r = _audio_go(client, world, 1, {"status": "heard", "window_low8": 3, "code": 1})
    assert "AUDIO_MISMATCH" in r["flags"]


def test_audio_ignored_when_session_has_no_audio(client, world):
    r = _audio_go(client, world, 0, {"status": "none_heard"})
    assert r["flags"] == [] and r["score"] == 1.0


def test_plan_tokens_and_log(client, world, monkeypatch):
    _burst(world)
    hp = {"Authorization": f"Bearer {world['prof']}"}
    hs = {"Authorization": f"Bearer {make_token(world['studs'][0], 'student', 'access')}"}
    for h in (hp, hs):
        p = client.get(f"/api/v1/sessions/{world['sid']}/audio-plan", headers=h).json()
        assert p["mode"] == "burst" and p["band_hz"] == [17500, 19000]
    t = client.get(f"/api/v1/sessions/{world['sid']}/tokens", headers=hp).json()["tokens"][0]
    assert t["audio_code"] == tokens.audio_code(world["secret"], world["sid"], t["window"])
    assert client.get(f"/api/v1/sessions/{world['sid']}/audio-plan").status_code == 401
    body = {"session_id": world["sid"], "data": {"peak": 0.6}}
    assert client.post("/api/v1/trial/audio-log", json=body, headers=hs).status_code == 204
    with SessionLocal() as db:
        from app.models import AudioLog
        assert db.query(AudioLog).filter(AudioLog.session_id == world["sid"]).count() == 0   # not trial mode: dropped
        monkeypatch.setattr(config, "TRIAL_MODE", True)
    assert client.post("/api/v1/trial/audio-log", json=body, headers=hs).status_code == 204
    with SessionLocal() as db:
        assert db.query(AudioLog).filter(AudioLog.session_id == world["sid"]).count() == 1
        db.query(AudioLog).filter(AudioLog.session_id == world["sid"]).delete(); db.commit()
