"""Run nightly from cron/systemd:   uv run python scripts/retention.py
At the end of a term:              uv run python scripts/retention.py --term-end COURSE_ID [COURSE_ID ...]"""
import argparse
import sys
sys.path.insert(0, ".")

from app import models  # noqa: F401
from app import retention
from app.db import SessionLocal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--term-end", nargs="+", metavar="COURSE_ID", help="delete face templates of every student on these courses")
    a = ap.parse_args()
    with SessionLocal() as db:
        print(retention.term_end(db, a.term_end) if a.term_end else retention.daily(db))


if __name__ == "__main__":
    main()
