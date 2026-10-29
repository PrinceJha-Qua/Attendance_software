# GateKeeper v1: Scope Fence, Interfaces, API, and 10 Phases

Supersedes the phase list in `docs/design/BUILD_PLAN.md` §6. Everything else in `docs/design/BUILD_PLAN.md` (spec corrections §0, trust model §2, decisions §12) still stands. Audio now follows `docs/research/ultrasonic-audio-reliability.md`.

---

## 0. Rules of engagement for Claude Code (the anti-berserk rules)

1. **Time-box.** Each phase has a hard budget (hours). At the limit, stop and ship what works. Overrun is handled by cutting from the phase's *Cut list*, never by extending.
2. **Scope fence.** Build only what is under the phase's *Build* heading. Anything not listed is out of scope. Ideas go to `docs/PARKING_LOT.md` (one line each) and are not built.
3. **"Done" means the exit checklist is true**, not "perfect". No refactors, renames, or polish outside the phase.
4. **Fixed stack, no new dependencies** without asking: FastAPI, SQLAlchemy 2 + Alembic, Postgres (SQLite in dev), Redis (fakeredis in dev), React + Vite + TypeScript, plain CSS (one small stylesheet; no UI framework, no design system), zxing-wasm, onnxruntime-web, Web Audio.
5. **UI is functional-minimal:** readable, mobile-first, no animations except what a feature needs (QR countdown, colour flash). No theming, no dark mode, no i18n in v1.
6. **Blocked for 30 minutes → stop and report** with the facts. Don't invent workarounds that change scope.
7. **Testing split.** Claude Code does **not** write or run the test suites. It writes `docs/acceptance/P{n}.md` (acceptance criteria and test cases) and a phase handoff note. **Antigravity (AGY)** writes and runs all unit/API/E2E/load/security tests and returns a pass/fail report. Claude Code may only smoke-start the app to check it boots. Next phase starts only after AGY's report is green or the failures are triaged.
8. **One source of truth for the API:** `docs/openapi.json` is generated in P1 and only changed by adding what §4 lists.

---

## 1. Interfaces (exactly three)

| # | Interface | Device | What it is |
|---|---|---|---|
| A | **Student app** | Phone browser, installable PWA | Check-in + personal attendance dashboard. Optimised for phones: ≤ 200 KB JS before the face model, loads in ≤ 2 s on 4G, one-thumb layout |
| B | **Professor app** | Laptop browser | Course/roster setup, enrollment confirmation, run a session (two routes: **Projection** `/project/:id` shown on the projector, **Console** `/session/:id` on the laptop screen / private), reports, disputes |
| C | **Backend API + ops scripts** | VM | FastAPI service; admin tasks are CLI scripts (seed users, create courses), **no admin UI in v1** |

No separate room-display app, no native app, no TV app (the Projection route is opened full-screen on whatever the room has).

---

## 2. Feature list (v1 is exactly this)

### Student app
1. Login (email/roll no + password), session stays signed in.
2. One-time **consent + enrollment**: reference selfie (on-device embedding + stored encrypted reference photo), then wait for professor confirmation.
3. **Check-in**: scan QR → face + colour-flash liveness → audio listen (self-test first time) → result (Verified / Needs confirmation / Try again). If audio is unavailable the check-in still succeeds with a flag.
4. **Attendance dashboard**: per course, attendance %, status (Safe / Borderline / Below), **how many more classes you can skip and still stay ≥ required %** (default 75%), or **how many you must attend in a row to get back to required** (§3).
5. **History** per course (date, status) and **Dispute** button on any absent record.
6. **Privacy page**: what's stored, delete my face data.

### Professor app
1. Login; list my courses.
2. **Course setup**: name, planned total sessions (optional), required % (default 75), roster CSV upload.
3. **Enrollment confirm** screen: pending students with their reference photo → *Confirmed / Reject* (professor has seen the ID card).
4. **Session start**: pick course, start. **Projection route**: rotating QR + hidden audio emitter + start/stop audio. 
5. **Live console**: counts (checked-in / roster), check-in rate, **Attention Panel** (flagged + overdue students with ID photo, reason, *Seen / Not here / Unsure*), random audit suggestions, manual add (with reason), pause/close.
6. **After class**: session summary and CSV export; course report (attendance % per student); disputes inbox (decide Accept/Reject).

### Explicitly NOT in v1 (parking lot)
Native app, SIS/LMS sync, timetable auto-start, Wi-Fi/AP signals, peer-chirp proximity graph, server-side face check, admin UI, i18n, dark mode, push notifications (mid-class re-check uses an in-app prompt only if the app is open), seat maps, analytics dashboards, multi-professor courses, parent/guardian views, XLSX export, email notifications.

---

## 3. The 75% calculator (student dashboard, server-computed)

Inputs per course: `A` attended (present + excused-if-counted), `T` classes conducted so far, `r` required fraction (default 0.75), `S` planned total (optional).

- **Current %** = `100·A/T` (show "–" if T = 0).
- **If A/T ≥ r (safe)**: classes you can still miss, *assuming you attend all others from now*:
  - Using sessions conducted only: `skip_now = floor(A/r − T)`. This is the number you can miss **before the next class** and stay ≥ r (counting those missed classes as conducted).
  - If `S` is set: `must_attend = max(0, ceil(r·S) − A)`, `remaining = S − T`, `can_skip_until_end = remaining − must_attend` (never below 0). **Show this one when S is set**, it is the accurate "holidays left" number.
- **If A/T < r (below)**: `need_in_a_row = ceil((r·T − A)/(1 − r))` consecutive classes to attend to get back to r (with S set, also show if it is still reachable: `A + remaining ≥ ceil(r·S)`).
- **Status**: *Safe* if skip_now ≥ 2, *Borderline* if 0–1, *Below* if A/T < r.
- Example: A=27, T=34, r=0.75 → 79.4%, skip_now = floor(36 − 34) = 2. A=20, T=30 → 66.7%, need_in_a_row = ceil((22.5−20)/0.25) = 10.
- Edge cases: T=0, S<T (ignore S), excused counted as attended by default (`course.excused_counts` toggle), unresolved REVIEW at session close counts as **present** (benefit of the doubt) and appears in the professor's "unreviewed flags" list.

---

## 4. API v1 (final; nothing else gets built)

Base `/api/v1`, JSON, JWT. Errors `{error:{code,message,request_id}}`. Mutations accept `Idempotency-Key`.

**Auth / identity**
| Method | Path | Who |
|---|---|---|
| POST | `/auth/login` · `/auth/refresh` · `/auth/logout` | both |
| GET | `/me` | both |
| GET | `/time` | both (clock sample) |

**Student: enrollment and privacy**
| Method | Path | Purpose |
|---|---|---|
| POST | `/students/me/consent` | record consent text version |
| POST | `/students/me/enroll` | multipart: reference photo + 128-float vector + model id |
| GET | `/students/me/enrollment` | `none/pending/confirmed/rejected` |
| DELETE | `/students/me/face-data` | delete vector + photo |

**Student: check-in**
| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions/active` | live sessions for my courses |
| POST | `/sessions/{id}/challenge` | one-time server challenge + colour-flash sequence + audio hints; valid 15 s |
| POST | `/sessions/{id}/checkin` | the core call (QR, challenge id, face vector + liveness result, audio result, device info) |
| GET | `/sessions/{id}/checkin/me` | my status |
| POST | `/trial/audio-log` | field-trial only: upload audio SNR/self-test numbers (off in prod unless `TRIAL_MODE`) |

**Student: dashboard**
| Method | Path | Purpose |
|---|---|---|
| GET | `/students/me/courses` | each course: %, A, T, status, skip_now / can_skip_until_end / need_in_a_row |
| GET | `/students/me/courses/{cid}/attendance` | summary + per-session history |
| POST | `/students/me/attendance/{record_id}/dispute` | `{note}` |
| GET | `/students/me/disputes` | my disputes and outcomes |

**Professor: setup**
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/courses` | list / create |
| PATCH | `/courses/{id}` | `planned_total_sessions`, `required_pct`, `excused_counts` |
| POST | `/courses/{id}/roster/import` | CSV |
| GET | `/courses/{id}/roster` | roster + enrollment state |
| GET | `/courses/{id}/enrollments/pending` | with signed photo URLs |
| POST | `/courses/{id}/enrollments/{student_id}/confirm` · `/reject` | |

**Professor: session**
| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | create `{course_id, step_ms, audio_mode}` |
| GET | `/sessions/{id}` | state + counts |
| POST | `/sessions/{id}/start` · `/pause` · `/resume` · `/close` | lifecycle |
| GET | `/sessions/{id}/tokens?from=&count=` | signed QR payloads to render locally |
| GET | `/sessions/{id}/audio-plan` | burst schedule for the emitter |
| GET | `/sessions/{id}/attendance` | live list |
| GET | `/sessions/{id}/attention` | ranked flagged + overdue (with signed photo URLs) |
| GET | `/sessions/{id}/spotcheck/suggestions` | top-k + random audit sample |
| POST | `/sessions/{id}/spotcheck/{student_id}` | `seen/not_seen/unsure` |
| POST | `/sessions/{id}/attendance/{student_id}/resolve` | `present/absent` + note |
| POST | `/sessions/{id}/attendance/manual` | add with reason |
| GET | `/sessions/{id}/export?format=csv` | |
| WS | `/ws/sessions/{id}` | events: `checkin.created/updated`, `attention.raised/cleared`, `session.state`, `stats.tick` |

**Professor: reports and disputes**
| Method | Path | Purpose |
|---|---|---|
| GET | `/courses/{id}/report` | per-student % and unreviewed flags |
| GET | `/courses/{id}/disputes` | inbox |
| POST | `/disputes/{id}/decide` | `accept/reject` + note |

**Ops**: `GET /healthz`, `GET /readyz`, `GET /students/{id}/photo?sig=` (signed, ≤ 5 min, professor of that session only, audit-logged).
Total: about 45 endpoints. Check-in outcome: `VERIFIED | REVIEW | REJECTED`; audio missing never rejects (flag `AUDIO_UNAVAILABLE`). Error codes as in `docs/design/BUILD_PLAN.md` §3.3.

---

## 5. The 10 phases

Total software budget: **≈ 46 hours (2 days)**. Each phase ends with an AGY test pass and a gate. Field trials are after Phase 10.

### P1. Foundation and contracts (4 h)
- **Build:** repo layout; Postgres/SQLite models and migrations for all tables in §4; seed script (1 professor, 1 course, 30 students); auth (JWT, two roles); OpenAPI generated with **all §4 endpoints stubbed** returning 501; CI config; token service (already done) wired in.
- **Cut list if late:** CI config, Alembic (use create_all).
- **Not now:** any real endpoint logic.
- **Exit:** app boots; `docs/openapi.json` matches §4; login works for both roles.
- **AGY:** contract test that every §4 endpoint exists with correct auth rules (401/403).

### P2. Courses, roster, enrollment (4 h)
- **Build:** course CRUD, roster CSV import, student consent + enroll (reference photo encrypted at rest, vector stored), professor pending-enrollment screen with confirm/reject; student enrollment wizard screens (login, consent, selfie, waiting).
- **Cut list:** photo resize, retake counter.
- **Exit:** a student can enroll; professor confirms; delete-my-face-data works.
- **AGY:** API tests for roster import edge cases, enrollment state machine, photo access only by the owning professor, deletion actually deletes.

### P3. Session lifecycle + Projection route (4 h)
- **Build:** session create/start/pause/resume/close; token batches; Projection page (fullscreen rotating QR from prefetched tokens, countdown ring, nothing else); `GET /time`.
- **Cut list:** QR size presets (ship one size slider).
- **Exit:** QR rotates on a projector-like browser window, tokens verify server-side.
- **AGY:** token tests (valid, grace, expired, forged, wrong session), lifecycle state machine, 240 token batch fetches.

### P4. Student check-in core (QR only) (5 h)
- **Build:** student scan screen (zxing-wasm), `/challenge` (issue only), `/checkin` with QR gate + per-student nonce + idempotency + roster/enrollment checks, result screen; session active list. No face/audio yet.
- **Cut list:** torch/zoom controls.
- **Exit:** two real phones check in against a real projection; duplicate scan returns the same result.
- **AGY:** E2E with fake camera video of a QR; concurrency test 240 check-ins in 15 s; replay and relay-delay cases; load targets from `docs/design/BUILD_PLAN.md` §5 B-PERF-01/03.

### P5. Face layer + trust scorer (5 h)
- **Build:** on-device face embedding (one model, fixed), match to enrolled template, in-session collision check, colour-flash liveness, trust scorer producing VERIFIED/REVIEW/REJECTED + flags, per-device passkey binding (single option: platform passkey).
- **Cut list:** passkey binding (becomes v1.1), motion-sensor signals.
- **Not now:** threshold tuning beyond one config value each.
- **Exit:** wrong-person phone and 2-phones-one-face are flagged REVIEW on real devices.
- **AGY:** mule simulation (same vector under 3 IDs), wrong-person vectors, liveness fail path, face-optional consent path, privacy check that no image bytes appear in any check-in request.

### P6. Audio layer (5 h) (follows the reliability report)
- **Build:** emitter in Projection route (chirp preamble + spread-spectrum payload, 17.5–19 kHz, ramped bursts, low level, user-gesture start); receiver in AudioWorklet (matched filter, processing flags off); first-run per-phone self-test; audio result in `/checkin` that adds score only; `AUDIO_UNAVAILABLE` flag path; `/audio-plan`; `/trial/audio-log`.
- **Cut list:** second frequency slot, lower-band tier (keep as config stub).
- **Not now:** peer chirps, FEC beyond CRC + repetition.
- **Exit:** on 2 devices in a room, code decodes at ≥ 3 m; phone with no audio still checks in.
- **AGY:** E2E with injected audio files (clean, noisy, silent, replayed old code, wrong session code), spoof test (static tone must fail), self-test states, mic-denied path.

### P7. Professor live console (5 h)
- **Build:** WebSocket feed, live counts, Attention Panel with signed ID photos, spot-check endpoints and audit suggestions, resolve, manual add, session close that finalises attendance records (rules in §3 last bullet), CSV export.
- **Cut list:** check-in rate chart (show a number), audit sample (keep top-k only).
- **Exit:** a flagged student appears on the panel in ≤ 500 ms; close generates records.
- **AGY:** WS reconnect/replay, panel hidden on projection route, photo URL expiry and cross-professor 403, resolve/close idempotency, export content equals DB.

### P8. Student dashboard + disputes (4 h)
- **Build:** `/students/me/courses`, per-course history, the §3 calculator (server-side), dispute create/list, professor course report + disputes inbox/decide.
- **Cut list:** per-month filters, charts.
- **Exit:** the §3 worked examples return exactly the stated numbers.
- **AGY:** table-driven calculator tests (all edge cases in §3), dispute lifecycle, dashboard numbers match exports.

### P9. Hardening, privacy, deploy (5 h)
- **Build:** rate limits, uniform error messages, JWT/CORS/CSP headers, retention job (delete vectors/photos at term end), audit log completeness, deployment to the India-region VM with HTTPS and Redis/Postgres, nightly backup, `TRIAL_MODE` switch, simple structured logs.
- **Cut list:** backup automation (document a manual command).
- **Exit:** production-like deploy reachable over HTTPS from a phone; `.env` secrets only on the VM.
- **AGY:** security suite (IDOR, JWT tampering, injection basics, rate limits), chaos (Redis restart, DB slow), full load run B-PERF-01..06, soak.

### P10. Freeze, full regression, field kit (5 h)
- **Build:** bug fixes from AGY reports only, seed demo data, short README + runbook, field-trial checklist (`docs/FIELD_PROTOCOL.md` from `docs/design/BUILD_PLAN.md` §7 plus the audio measurement plan in the reliability report), tag `v1.0-rc1`.
- **Cut list:** everything except the above.
- **Exit:** AGY full regression green across Chrome Android + Safari iOS; RC deployed; known-issues list written.

**Schedule:** P1–P3 = day 1 morning (12 h wall-clock at ~4 h each), P4–P6 = day 1 afternoon/evening, P7–P10 = day 2. The 48-hour limit is a target; if any gate fails, we fix before moving on rather than starting the next phase.

---

## 6. Hand-offs
- Claude Code writes `docs/acceptance/P{n}.md` at the **start** of each phase (so AGY can prepare tests in parallel): list of endpoints/screens touched, acceptance criteria, edge cases, and the exact numbers to check.
- AGY returns `docs/reports/AGY_P{n}.md` (pass/fail matrix, failures with request/response evidence).
- You decide go/no-go at each gate. Claude Code never self-approves a gate.
