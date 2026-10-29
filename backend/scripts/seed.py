"""Dev seed: 1 professor, 1 course, 30 students on the roster. Idempotent. DEV PASSWORDS ONLY."""
import os
import sys
sys.path.insert(0, ".")

from sqlalchemy import select

from app import models
from app.db import Base, SessionLocal, engine
from app.security import hash_password

DEV_PASSWORD = "gatekeeper-dev"


def main():
    if os.environ.get("GK_ENV") == "prod":
        sys.exit("seed.py is dev-only; use scripts/create_user.py")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(models.User).where(models.User.email == "prof@example.edu")):
            print("already seeded")
            return
        pw = hash_password(DEV_PASSWORD)
        prof = models.User(email="prof@example.edu", name="Dev Professor", role="professor", password_hash=pw)
        db.add(prof)
        db.flush()
        course = models.Course(professor_id=prof.id, name="CS101 Intro to Computing", planned_total_sessions=40)
        db.add(course)
        db.flush()
        for i in range(1, 31):
            s = models.User(email=f"student{i:02d}@example.edu", roll_no=f"R{i:03d}", name=f"Student {i:02d}", role="student", password_hash=pw)
            db.add(s)
            db.flush()
            db.add(models.RosterEntry(course_id=course.id, student_id=s.id))
        db.commit()
    print(f"seeded. login: prof@example.edu / student01@example.edu, password '{DEV_PASSWORD}' (dev only)")


if __name__ == "__main__":
    main()
