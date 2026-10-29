"""Phase 8 builder checks: dashboard numbers, dispute lifecycle, report. Own users/course, cleaned up after."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AttendanceRecord, AuditLog, ClassSession, Course, Dispute, RosterEntry, User
from app.security import make_token


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


@pytest.fixture
def w():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        prof = db.scalar(select(User).where(User.email == "prof@example.edu"))
        other = User(email=f"p8o_{tag}@example.edu", roll_no=f"O8{tag}", name="Other", role="professor", password_hash="x")
        a = User(email=f"p8a_{tag}@example.edu", roll_no=f"A8{tag}", name="Alice", role="student", password_hash="x")
        b = User(email=f"p8b_{tag}@example.edu", roll_no=f"B8{tag}", name="Bob", role="student", password_hash="x")
        db.add_all([other, a, b])
        c = Course(professor_id=prof.id, name=f"P8 {tag}", planned_total_sessions=12)
        db.add(c); db.flush()
        db.add_all([RosterEntry(course_id=c.id, student_id=a.id), RosterEntry(course_id=c.id, student_id=b.id)])
        # 8 closed sessions: Alice present in 6, absent in 2; Bob absent in all
        recs, sids = {}, []
        for n in range(8):
            s = ClassSession(course_id=c.id, status="closed", secret=b"k" * 32, started_at=datetime.now(timezone.utc) - timedelta(days=8 - n), closed_at=datetime.now(timezone.utc) - timedelta(days=8 - n))
            db.add(s); db.flush(); sids.append(s.id)
            ra = AttendanceRecord(session_id=s.id, student_id=a.id, status="absent" if n in (3, 5) else "present", source="checkin" if n != 5 else "default")
            rb = AttendanceRecord(session_id=s.id, student_id=b.id, status="absent", source="default")
            db.add_all([ra, rb]); db.flush(); recs[n] = (ra.id, rb.id)
        db.commit()
        out = dict(course=c.id, sids=sids, a=a.id, b=b.id, recs=recs, ta=make_token(a.id, "student", "access"), tb=make_token(b.id, "student", "access"),
                   prof=make_token(prof.id, "professor", "access"), other=make_token(other.id, "professor", "access"), other_id=other.id)
    yield out
    with SessionLocal() as db:
        rid = list(db.scalars(select(AttendanceRecord.id).where(AttendanceRecord.session_id.in_(out["sids"]))))
        db.query(Dispute).filter(Dispute.record_id.in_(rid)).delete()
        db.query(AttendanceRecord).filter(AttendanceRecord.session_id.in_(out["sids"])).delete()
        db.query(ClassSession).filter(ClassSession.course_id == out["course"]).delete()
        db.query(RosterEntry).filter(RosterEntry.course_id == out["course"]).delete()
        db.query(AuditLog).filter(AuditLog.actor_id.in_([out["a"], out["b"]])).delete()
        db.query(Course).filter(Course.id == out["course"]).delete()
        db.query(User).filter(User.id.in_([out["a"], out["b"], out["other_id"]])).delete()
        db.commit()


H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


def test_dashboard_numbers(client, w):
    c = client.get("/api/v1/students/me/courses", headers=H(w["ta"])).json()
    mine = next(x for x in c if x["course_id"] == w["course"])
    assert (mine["attended"], mine["conducted"], mine["pct"], mine["status"]) == (6, 8, 75.0, "borderline")
    assert mine["skip_now"] == 0 and mine["can_skip_until_end"] == 1   # S=12, need ceil(9)=9, has 6, remaining 4 -> must 3, skip 1
    b = next(x for x in client.get("/api/v1/students/me/courses", headers=H(w["tb"])).json() if x["course_id"] == w["course"])
    assert b["status"] == "below" and b["need_in_a_row"] == 24 and b["reachable"] is False
    h = client.get(f"/api/v1/students/me/courses/{w['course']}/attendance", headers=H(w["ta"])).json()
    assert len(h["history"]) == 8 and sum(x["can_dispute"] for x in h["history"]) == 2 and h["summary"]["pct"] == 75.0
    assert client.get(f"/api/v1/students/me/courses/{w['course']}/attendance", headers=H(w["prof"])).status_code == 403
    assert client.get("/api/v1/students/me/courses/nope/attendance", headers=H(w["ta"])).status_code == 404


def test_dispute_lifecycle(client, w):
    rid = w["recs"][3][0]
    bad = client.post(f"/api/v1/students/me/attendance/{w['recs'][0][0]}/dispute", json={"note": "I was there"}, headers=H(w["ta"]))
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "NOT_DISPUTABLE"       # present record
    assert client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "x"}, headers=H(w["ta"])).status_code == 422
    assert client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "I was there"}, headers=H(w["tb"])).status_code == 404   # someone else's record
    r = client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "I was there, sat in row 3"}, headers=H(w["ta"])); assert r.status_code == 201
    did = r.json()["id"]
    assert client.post(f"/api/v1/students/me/attendance/{rid}/dispute", json={"note": "again again"}, headers=H(w["ta"])).json()["error"]["code"] == "DISPUTE_EXISTS"
    inbox = client.get(f"/api/v1/courses/{w['course']}/disputes", headers=H(w["prof"])).json()
    assert len(inbox) == 1 and inbox[0]["student"]["name"] == "Alice" and inbox[0]["record_note"] is None
    assert client.get(f"/api/v1/courses/{w['course']}/disputes", headers=H(w["other"])).status_code == 404
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept"}, headers=H(w["other"])).status_code == 404
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept", "note": "saw them"}, headers=H(w["prof"])).json()["record_status"] == "present"
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "accept"}, headers=H(w["prof"])).status_code == 200      # idempotent
    assert client.post(f"/api/v1/disputes/{did}/decide", json={"decision": "reject"}, headers=H(w["prof"])).json()["error"]["code"] == "ALREADY_DECIDED"
    mine = next(x for x in client.get("/api/v1/students/me/courses", headers=H(w["ta"])).json() if x["course_id"] == w["course"])
    assert mine["attended"] == 7 and mine["pct"] == 87.5                                          # accepted -> counts
    d = client.get("/api/v1/students/me/disputes", headers=H(w["ta"])).json()
    assert d[0]["status"] == "accepted" and d[0]["decided_note"] == "saw them"
    # rejected path leaves the record absent
    rid2 = w["recs"][5][0]
    did2 = client.post(f"/api/v1/students/me/attendance/{rid2}/dispute", json={"note": "I was there too"}, headers=H(w["ta"])).json()["id"]
    assert client.post(f"/api/v1/disputes/{did2}/decide", json={"decision": "reject", "note": "not seen"}, headers=H(w["prof"])).json()["record_status"] == "absent"
    assert client.get(f"/api/v1/courses/{w['course']}/disputes?status=open", headers=H(w["prof"])).json() == []
    assert len(client.get(f"/api/v1/courses/{w['course']}/disputes?status=all", headers=H(w["prof"])).json()) == 2


def test_course_report(client, w):
    r = client.get(f"/api/v1/courses/{w['course']}/report", headers=H(w["prof"])).json()
    assert r["sessions_conducted"] == 8 and r["open_session_id"] is None and len(r["students"]) == 2
    alice = next(s for s in r["students"] if s["name"] == "Alice")
    assert alice["pct"] == 75.0 and alice["unreviewed_flags"] == 0
    assert client.get(f"/api/v1/courses/{w['course']}/report", headers=H(w["other"])).status_code == 404
    assert client.get(f"/api/v1/courses/{w['course']}/report", headers=H(w["ta"])).status_code == 403
