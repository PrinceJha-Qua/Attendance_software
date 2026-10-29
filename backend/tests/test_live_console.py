"""Phase 7 checks written by the builder (AGY owns the acceptance suite). Own users/course, cleaned up after."""
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
from app.main import app
from app.models import AttendanceRecord, AuditLog, Challenge, Checkin, ClassSession, Course, FaceTemplate, RosterEntry, Spotcheck, User
from app.security import hash_password, make_token

M = config.FACE_MODEL
vec = lambda x, b=0.0: [x + (b if i == 0 else 0.0) for i in range(128)]  # noqa: E731


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture
def w():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        other = User(email=f"p7o_{tag}@example.edu", roll_no=f"O{tag}", name="Other Prof", role="professor", password_hash="x")
        db.add(other)
        course = Course(professor_id=prof.id, name=f"P7 {tag}")
        db.add(course); db.flush()
        studs = []
        for n in range(5):
            u = User(email=f"p7_{tag}_{n}@example.edu", roll_no=f"=P7{tag}{n}" if n == 4 else f"P7{tag}{n}", name=f"P7 Student {n}", role="student", password_hash=hash_password("x"))
            db.add(u); db.flush()
            db.add(FaceTemplate(student_id=u.id, vector=vec(0.1 * (n + 1)), model=M, status="confirmed", photo_enc=b"x" if n == 0 else None))
            db.add(RosterEntry(course_id=course.id, student_id=u.id))
            studs.append(u.id)
        s = ClassSession(course_id=course.id, status="live", step_ms=1500, secret=b"k" * 32, started_at=datetime.now(timezone.utc))
        db.add(s); db.commit()
        out = dict(course=course.id, sid=s.id, secret=s.secret, studs=studs, prof=make_token(prof.id, "professor", "access"), other=make_token(other.id, "professor", "access"), other_id=other.id)
    yield out
    with SessionLocal() as db:
        sids = list(db.scalars(select(ClassSession.id).where(ClassSession.course_id == out["course"])))
        for model in (AttendanceRecord, Spotcheck, Checkin, Challenge):
            db.query(model).filter(model.session_id.in_(sids)).delete()
        db.query(ClassSession).filter(ClassSession.course_id == out["course"]).delete()
        db.query(RosterEntry).filter(RosterEntry.course_id == out["course"]).delete()
        db.query(FaceTemplate).filter(FaceTemplate.student_id.in_(out["studs"])).delete()
        db.query(AuditLog).filter(AuditLog.actor_id.in_([*out["studs"], out["other_id"]])).delete()
        db.query(Course).filter(Course.id == out["course"]).delete()
        db.query(User).filter(User.id.in_([*out["studs"], out["other_id"]])).delete()
        db.commit()


def P(w, who="prof"): return {"Authorization": f"Bearer {w[who]}"}


def checkin(client, w, i, face):
    h = {"Authorization": f"Bearer {make_token(w['studs'][i], 'student', 'access')}"}
    ch = client.post(f"/api/v1/sessions/{w['sid']}/challenge", headers=h).json()
    base = [120] * 3
    body = {"qr": tokens.make_qr_payload(w["secret"], w["sid"], tokens.window_for(int(time.time() * 1000), 1500)), "challenge_id": ch["challenge_id"],
            "face": {"vector": face, "model": M}, "liveness": {"baseline": base, "samples": [[120 + 0.2 * (trust.RGB[c][k] - 85) for k in range(3)] for c in ch["colour_seq"]]}}
    return client.post(f"/api/v1/sessions/{w['sid']}/checkin", json=body, headers=h).json()


def test_close_writes_records_once_and_review_counts_present(client, w):
    checkin(client, w, 0, vec(0.1))                # VERIFIED
    assert checkin(client, w, 1, vec(0.9))["status"] == "REVIEW"   # wrong face -> REVIEW
    r = client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w)); assert r.status_code == 200 and r.json()["status"] == "closed"
    again = client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w)); assert again.status_code == 200
    with SessionLocal() as db:
        recs = {r.student_id: r for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == w["sid"]))}
    assert len(recs) == 5
    assert recs[w["studs"][0]].status == "present" and recs[w["studs"][1]].status == "present" and recs[w["studs"][1]].note.startswith("unreviewed")
    assert recs[w["studs"][2]].status == "absent" and recs[w["studs"][2]].source == "default"
    assert client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w, "other")).status_code == 404


def test_cannot_close_before_start(client, w):
    with SessionLocal() as db:
        db.get(ClassSession, w["sid"]).status = "created"; db.commit()
    assert client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w)).json()["error"]["code"] == "INVALID_STATE"


def test_one_open_session_per_course(client, w):
    r = client.post("/api/v1/sessions", json={"course_id": w["course"]}, headers=P(w))
    assert r.status_code == 409 and r.json()["error"]["code"] == "OPEN_SESSION_EXISTS"
    client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w))
    assert client.post("/api/v1/sessions", json={"course_id": w["course"]}, headers=P(w)).status_code == 201


def test_attention_flagged_photo_and_verdicts(client, w):
    checkin(client, w, 0, vec(0.9))   # student 0 has a photo; wrong face -> REVIEW
    a = client.get(f"/api/v1/sessions/{w['sid']}/attention", headers=P(w)).json()
    assert a["items"][0]["kind"] == "flagged" and a["items"][0]["student_id"] == w["studs"][0] and a["items"][0]["photo_url"].startswith("/api/v1/students/")
    assert "Face does not match the enrolled photo" in a["items"][0]["reasons"]
    assert client.get(f"/api/v1/sessions/{w['sid']}/attention", headers=P(w, "other")).status_code == 404
    r = client.post(f"/api/v1/sessions/{w['sid']}/spotcheck/{w['studs'][0]}", json={"result": "seen"}, headers=P(w)); assert r.status_code == 200
    assert client.get(f"/api/v1/sessions/{w['sid']}/attention", headers=P(w)).json()["items"] == []     # card cleared
    att = client.get(f"/api/v1/sessions/{w['sid']}/attendance", headers=P(w)).json()
    assert next(x for x in att["students"] if x["student_id"] == w["studs"][0])["display"] == "present"


def test_overdue_after_grace_and_audit_sample(client, w, monkeypatch):
    checkin(client, w, 0, vec(0.1))
    assert client.get(f"/api/v1/sessions/{w['sid']}/attention", headers=P(w)).json()["items"] == []     # inside the grace period
    with SessionLocal() as db:
        db.get(ClassSession, w["sid"]).started_at = datetime.now(timezone.utc) - timedelta(seconds=config.ATTENTION_GRACE_S + 5); db.commit()
    a = client.get(f"/api/v1/sessions/{w['sid']}/attention?k=3", headers=P(w)).json()
    assert len(a["items"]) == 3 and a["total_open"] == 4 and all(i["kind"] == "overdue" for i in a["items"])
    sg = client.get(f"/api/v1/sessions/{w['sid']}/spotcheck/suggestions", headers=P(w)).json()["items"]
    assert [i for i in sg if i["kind"] == "audit"][0]["student_id"] == w["studs"][0]
    # audit sample says not seen -> confirmed false accept is counted
    client.post(f"/api/v1/sessions/{w['sid']}/spotcheck/{w['studs'][0]}", json={"result": "not_seen"}, headers=P(w))
    s = client.get(f"/api/v1/sessions/{w['sid']}/attendance", headers=P(w)).json()["summary"]
    assert s["audit_sampled"] == 1 and s["audit_not_seen"] == 1


def test_resolve_manual_and_validation(client, w):
    sid = w["sid"]
    r = client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": w["studs"][3], "reason": "ab"}, headers=P(w)); assert r.status_code == 422
    r = client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": w["studs"][3], "reason": "phone broken"}, headers=P(w)); assert r.status_code == 201
    r = client.post(f"/api/v1/sessions/{sid}/attendance/manual", json={"student_id": "nobody", "reason": "phone broken"}, headers=P(w)); assert r.status_code == 404
    for _ in range(2):   # idempotent
        assert client.post(f"/api/v1/sessions/{sid}/attendance/{w['studs'][2]}/resolve", json={"status": "absent", "note": "n"}, headers=P(w)).status_code == 200
    with SessionLocal() as db:
        assert db.query(AttendanceRecord).filter(AttendanceRecord.session_id == sid).count() == 2
    assert client.post(f"/api/v1/sessions/{sid}/attendance/{w['studs'][2]}/resolve", json={"status": "maybe"}, headers=P(w)).status_code == 422
    client.post(f"/api/v1/sessions/{sid}/close", headers=P(w))
    with SessionLocal() as db:   # close keeps the professor's decisions
        assert {r.source for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == sid))} == {"manual", "resolved", "default"}


def test_export_matches_db_and_blocks_formula_injection(client, w):
    checkin(client, w, 4, vec(0.5))
    client.post(f"/api/v1/sessions/{w['sid']}/close", headers=P(w))
    r = client.get(f"/api/v1/sessions/{w['sid']}/export?format=csv", headers=P(w))
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert r.headers["content-type"].startswith("text/csv") and len(rows) == 5
    assert all(not x["roll_no"].startswith("=") for x in rows) and any(x["roll_no"].startswith("'=") for x in rows)
    assert {x["status"] for x in rows} == {"present", "absent"}
    assert client.get(f"/api/v1/sessions/{w['sid']}/export?format=xlsx", headers=P(w)).status_code == 422


def test_websocket_feed_auth_events_and_replay(client, w):
    base = f"/api/v1/ws/sessions/{w['sid']}"
    for bad in (f"{base}?token=garbage", f"{base}?token={w['other']}"):
        with pytest.raises(Exception):
            with client.websocket_connect(bad) as ws:
                ws.receive_json()
    with client.websocket_connect(f"{base}?token={w['prof']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        checkin(client, w, 0, vec(0.9))     # REVIEW -> created + attention.raised
        seen = []
        while len({"checkin.created", "attention.raised"} - {e["type"] for e in seen}):
            seen.append(ws.receive_json())
        raised = next(e for e in seen if e["type"] == "attention.raised")
        assert raised["data"]["student_id"] == w["studs"][0] and raised["data"]["photo_url"]
        ws.send_text("ping")
        types = []
        for _ in range(6):
            types.append(ws.receive_json()["type"])
            if "pong" in types and "stats.tick" in types: break
        assert "pong" in types and "stats.tick" in types
        last = max(e["seq"] for e in seen)
    # reconnect with since_seq replays only what was missed
    checkin(client, w, 1, vec(0.2))
    with client.websocket_connect(f"{base}?token={w['prof']}&since_seq={last}") as ws:
        hello = ws.receive_json(); assert hello["data"]["resync"] is False
        ev = ws.receive_json(); assert ev["type"] == "checkin.created" and ev["data"]["student_id"] == w["studs"][1]
    with client.websocket_connect(f"{base}?token={w['prof']}&since_seq=999999") as ws:
        assert ws.receive_json()["data"]["resync"] is True
