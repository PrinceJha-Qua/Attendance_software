# Contributing

Private project; this file is the working agreement.

## Ground rules
1. **Scope fence.** v1 is exactly `docs/V1_SCOPE_AND_PHASES.md`. New ideas go to `docs/PARKING_LOT.md` as one line, not into code.
2. **Fixed stack.** FastAPI, SQLAlchemy 2, PostgreSQL/SQLite, Redis, React + Vite + TypeScript, plain CSS. Ask before adding a dependency and record it in `docs/DECISIONS.md`.
3. **Human final say.** Never add an automatic absence or rejection path.
4. **Audio and face are soft signals.** Only the QR and the roster are hard gates.
5. **No thresholds from gut feeling.** Changing a number in `config.py`/`trust.py` needs field data cited in `docs/DECISIONS.md`.

## Workflow
```bash
make setup && make seed
make dev           # while working
make test          # backend suite must stay green
make build         # both apps must type-check and build
make openapi       # if you changed the API; commit docs/openapi.json
```
- Tests live in `backend/tests/`. Each behaviour change gets a test; acceptance cases for a feature go in `docs/acceptance/`.
- Models change → there are no migrations yet: write one (or reset the dev DB) and note it in the PR.
- Errors use the envelope `{error:{code,message,request_id}}` with an UPPER_SNAKE code. Never return stack traces or internals.
- Anything that stores or shows personal data must be audited (`storage.audit`) and covered by retention.
- Comments explain *why*, not what. Match the surrounding style.

## Commits
Imperative subject line (`Add dispute decision endpoint`), body explains the reason. One logical change per commit. Tag releases `vMAJOR.MINOR.PATCH[-rcN]` and add a line to `CHANGELOG.md`.
