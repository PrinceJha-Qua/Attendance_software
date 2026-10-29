"""Create an account on a deployed server (the seed script is dev-only and refuses to run in prod).
   uv run python scripts/create_user.py professor you@college.edu "Dr Name"      -> prints a one-time password
Students are created by the roster CSV import, not here."""
import secrets
import string
import sys
sys.path.insert(0, ".")

from app import models
from app.db import Base, SessionLocal, engine
from app.security import hash_password
from app.storage import audit


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "professor":
        sys.exit(__doc__)
    _, _, email, name = sys.argv
    Base.metadata.create_all(engine)
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    with SessionLocal() as db:
        db.add(models.User(email=email.strip().lower(), name=name, role="professor", password_hash=hash_password(pw)))
        audit(db, None, "user.created.cli", email)
        db.commit()
    print(f"created {email}\nPassword (shown once): {pw}")


if __name__ == "__main__":
    main()
