# GateKeeper deployment runbook (India-region VM, one box)

Untested against a real VM: the stack files were written without Docker available. First deploy = a dry run; expect to fix small things.

## 1. Prerequisites
- Ubuntu 22.04+ VM in an India region (e.g. Mumbai/Hyderabad), 2 vCPU / 4 GB, ports 80 and 443 open, Docker + compose plugin.
- Two DNS A records pointing at the VM: the student host (`attend.…`) and the professor host (`prof.…`). Caddy fetches certificates on first start.
- The private repo checked out at `/opt/gatekeeper`.

## 2. First start
```
cd /opt/gatekeeper/deploy
cp .env.example .env && chmod 600 .env     # fill every value (generation commands are in the file)
docker compose up -d --build
docker compose exec backend python scripts/create_user.py professor you@college.edu "Dr Name"   # prints a one-time password
```
Then sign in at the professor host, create the course, import the roster CSV (students get one-time passwords there).

## 3. Checks after every deploy
- `curl -s https://<student-host>/api/v1/healthz` and `/api/v1/readyz` return `{"ok":true}`.
- `https://<host>/docs` is 404 in prod. Response headers include HSTS, CSP, nosniff.
- Log in, run a 2-minute session with one phone, close it, check the CSV.
- `docker compose logs backend | tail`: JSON lines, no tokens, no query strings.

## 4. Routine
- Nightly (cron): `deploy/backup.sh` (dump, 14-day rotation, then the daily retention job).
- End of term: `docker compose exec backend python scripts/retention.py --term-end <course_id> ...` deletes face vectors and reference photos of everyone on those courses. Attendance records stay.
- Update: `git pull && docker compose up -d --build`. Tables are created, never migrated: a schema change needs a written migration first (parking lot).
- Restore: `gunzip -c gk-DATE.sql.gz | docker compose exec -T db psql -U gk gk` on an empty database.

## 5. Field trials
Set `GK_TRIAL_MODE=1` in `.env` only for audio trials, then `docker compose up -d backend`. Set it back to 0 afterwards; telemetry ages out after 90 days.

## 6. Limits to know
- One backend worker by design (in-process live feed). Scale up the VM, not the worker count.
- Postgres has not been exercised by the automated suite (SQLite only). Run AGY's suites once against a throwaway Postgres before the first real class.
- Access to the VM, database and backups = access to student face data. Keep that list short and written down.
