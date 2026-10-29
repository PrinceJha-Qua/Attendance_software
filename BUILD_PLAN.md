# GateKeeper: 5-Phase Build Plan, API Contract, Test & Field-Trial Plan

> **Update 2026-10-26:** the phase list (§6), API (§3) and app scope (§4) are superseded by `docs/V1_SCOPE_AND_PHASES.md` (10 phases, tightly scoped). Everything else here still applies.

Source spec: `product_description_and_benchmarking_suite (1).md` (identical to `~/Developer/product_description_and_benchmarking_suite.md`).
Builder: Claude Code. Integration/E2E verification: Antigravity (AGY). Target: release candidate in 48 h, then classroom trials.

---

## 0. Spec review: what must change before building

Reading the spec as an engineer, six things would sink the build or the benchmark if kept as written.

| # | Spec says | Problem | Decision |
|---|-----------|---------|----------|
| 1 | Redis `SET nonce:token student_id NX EX 5` | The QR token is the **same for the whole room** in a given window. With this key only the first student per window succeeds. | Key is `nonce:{session}:{window}:{student_id}`; NX makes a **per-student** replay impossible. Same-token reuse across students is allowed (that is how a shared projector works). Replay across *sessions or windows* is what the TTL blocks. |
| 2 | "128D landmark ratios" (IPD, nose-lip, jaw) with cosine > 0.88 = same person | Landmark geometry has low identity entropy. Different people's normalised landmark vectors sit at cosine 0.95+ against each other. At 0.88 you flag half the class; raising the threshold makes you miss real mules. | Use a **real face embedding** (128-d, e.g. MobileFaceNet via ONNX Runtime Web, or face-api.js dlib 128-d) computed on device. Keep "128 numbers, no image leaves the phone". MobileFaceNet reports ~99.5% LFW verification ([paper](https://arxiv.org/abs/1804.07573)). Threshold is **calibrated from our own data** (Bench B-SEC-FACE), not fixed at 0.88. MediaPipe is still used for face detection, framing and liveness prompts. |
| 3 | Mule detection = cross-ID collision only | A mule detected only by collision is caught only if they scan 2+ phones. Better and cheaper: **match the live face against the account's own enrolled template**. A friend holding your phone fails immediately. | Add enrollment (Phase 2) and `face_match_enrolled` check. Collision stays as second layer. |
| 4 | Audio is a pure 19.5 kHz tone, pass if +18 dB | A steady tone can be played by anyone from any speaker, and PA feedback suppressors notch steady tones. Safari and some phones low-pass or process mic input regardless of constraints ([WebKit bug](https://bugs.webkit.org/show_bug.cgi?id=179411)). Spec text also says "FSK" in the diagram but "sine wave" in the mechanics. | Tiered audio: **Tier A** tone probe (Phase 1 spike only), **Tier B** short FSK bursts carrying a per-window code in the 18.5–19.8 kHz band. Audio is a **scored signal, never a hard gate**, until field data justifies it (see §6). |
| 5 | Live video-call relay is "blocked by Opus stripping 19.5 kHz" | Opus can be full-band; a relayed video call has ~200–500 ms latency, which fits inside a 1.5–3 s QR window. And a remote student has *their own* face and passkey, so face + WebAuthn pass. The only co-location evidence is audio, which is the weakest link. | State honestly: **no software-only scheme fully stops a live relay.** Mitigate with audio (soft), campus Wi-Fi/subnet signal, professor spot-check queue, and statistical anomaly flags (many check-ins with identical latency offset). Don't claim 0% FAR on this vector. |
| 6 | Targets like "FAR = 0%" | 0 failures in N trials only proves FAR < 3/N at 95% confidence (rule of three). 0% is unprovable. | Benchmarks report FAR/FRR **with 95% confidence intervals** and minimum sample sizes (§5). |

Smaller fixes: 140 seats → 120/240 seat targets; WebAuthn is a device-binding signal (it does not prove who is holding the phone); BarcodeDetector is not on iOS Safari, so ship ZXing-wasm / `zxing-cpp` as the baseline decoder.

---

## 1. Honest confidence assessment

"Will it work" depends on the component. These are my estimates *before* any field data; the Phase 1 spikes and the room trials exist to replace these numbers with measurements.

| Component | Confidence it meets its target | Why |
|---|---|---|
| Backend: token issue/verify, nonce, session, WS dashboard at 240 concurrent | **95%** | Standard FastAPI + Redis; load is trivial (240 req in 15 s ≈ 16 rps). |
| Rotating QR decoded by students at 120-seat room | **75%** | Projector refresh/rolling-shutter banding, glare, and ~1 s decode make a 1.5 s TTL tight. Mitigation: make TTL configurable (1.5 / 3 / 5 s), larger QR, error-correction M/Q. |
| Rotating QR at 240-seat, back rows | **55%** | Pixels-per-module at 25–30 m. Needs a large, high-contrast code on the biggest screen; may need a "scan from the aisle" fallback or a second repeater screen. |
| Rotating QR on big TV (normal classroom) | **85%** | Short distances, bright panels. Watch for 60 Hz PWM banding on camera. |
| Face-embedding collision / enrollment mismatch (mule defence) | **85%** | Mature technology. Risks: low light, masks, glasses, bias across skin tones (must measure). |
| Passive anti-spoof (printed photo / screen replay) | **40%** | Single-frame embeddings don't defeat replay. Active challenge (turn head / blink) raises this to ~75%; still not iBeta-grade. |
| Audio as a **hard** co-location gate, all three room types | **20–35%** | Unknown, and the main unknown in the product: TV/laptop/projector speakers often roll off above 16–18 kHz, PA chains have DSP/limiters, 19.5 kHz air attenuation and directionality hurt the back rows, phone mics and Safari processing vary. |
| Audio as a **soft** trust signal | **70%** | Works on a subset of device×room combos; value depends on measured pass rate. |
| Whole system as spec'd, hitting every target in all three room types | **~30%** | Mostly the audio and 240-seat QR risk. |
| MVP (QR + face embedding + soft audio + professor review queue) hitting "attendance ≤ 60 s for 120–240 students, mule defence works, audio informative" | **~75%** | This is what we're building. |

Two-day feasibility: the **software** is feasible in 48 h with Claude Code because the stack is small and the plan front-loads the risky parts. What 48 h cannot buy is field validation. That is Day 3+ and is the real answer to "does the technology work".

---

## 2. Architecture and stack (fixed, so Claude Code doesn't re-decide)

```
gatekeeper/
  backend/            FastAPI (Python 3.12), Redis, SQLite (dev) / Postgres (prod), SQLAlchemy 2, Alembic
  student-pwa/        Vite + React + TypeScript, PWA; zxing-wasm, onnxruntime-web (face), MediaPipe tasks (detect/liveness), Web Audio
  prof-console/       Vite + React + TypeScript; QR canvas, audio emitter, live dashboard (WS)
  shared/             OpenAPI-generated TS client, token/QR payload spec, constants
  bench/              k6 + Locust load tests, python eval scripts, field-trial logger, results/ (CSV + report)
  e2e/                Playwright suites owned by Antigravity
  docs/               openapi.json, THREAT_MODEL.md, FIELD_PROTOCOL.md, RESULTS.md
```

Why a separate `prof-console`: it needs a user gesture to start audio, wake-lock, fullscreen QR, and different resilience rules (it must survive the Wi-Fi flapping). Student and professor surfaces share only `shared/`.

Token design (rolling, offline-verifiable by the server only):
`window = floor(server_time_ms / step_ms)`; `tok = base32(HMAC_SHA256(session_secret, session_id || window))[:16]`. The QR encodes `gk1:{session_id}:{window}:{tok}` (short, so a low-version QR). Server accepts windows `{now_window, now_window-1}` (tunable `grace_windows`). `session_secret` is per session, never the global secret. Professor console fetches signed tokens ahead of time in batches and renders locally, so QR display doesn't depend on a round trip every 1.5 s.

Trust result per check-in:

| Signal | Hard gate? | Effect |
|---|---|---|
| Token valid and in window | Yes | else `410 TOKEN_EXPIRED` / `403 TOKEN_INVALID` |
| Student on roster, not already checked in | Yes | `409 ALREADY_CHECKED_IN` (idempotent) |
| Face matches enrolled template | No (flag) | mismatch → `REVIEW` |
| Face collision with another student this session | No (flag) | both → `REVIEW` + alert |
| Liveness challenge passed | No (flag) | fail → `REVIEW` |
| Passkey assertion valid | No (score) | missing → lower score |
| Audio code heard | No (score) | missing → lower score, never reject alone |
| Campus subnet / Wi-Fi hint | No (score) | informational |

Output: `VERIFIED` (≥ 0.8), `REVIEW` (0.5–0.8 or any flag), `REJECTED` (hard gate failed). Professor resolves `REVIEW` items in one click. Weights live in config and are tuned from field data.

---

## 3. API contract (finalised)

Base: `/api/v1`. JSON. Auth: short-lived JWT (student: after login; professor: after login). All times are server epoch ms. Errors: `{ "error": { "code": "TOKEN_EXPIRED", "message": "...", "request_id": "..." } }`. Every mutating endpoint accepts `Idempotency-Key`.

### 3.1 Auth and identity
| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/auth/login` | both | Email/roll no + password or university SSO callback → JWT + role |
| POST | `/auth/refresh` | both | Refresh JWT |
| GET | `/me` | both | Profile, role, enrollment status |

### 3.2 Student enrollment (once per student)
| Method | Path | Purpose |
|---|---|---|
| POST | `/students/me/face-template` | Upload **128-float enrollment vector** (and model version). No images. Replaces existing only after re-auth |
| DELETE | `/students/me/face-template` | Delete template (privacy / consent withdrawal) |
| POST | `/students/me/passkey/register/options` · `/verify` | WebAuthn registration |
| GET | `/students/me/consent` · POST `/students/me/consent` | Record biometric-processing consent (required before enrollment) |

### 3.3 Student check-in
| Method | Path | Purpose |
|---|---|---|
| GET | `/time` | Server clock sample for client skew estimate (used to show "scan now" hints; the server never trusts client time) |
| GET | `/sessions/active` | Sessions the student can join today (course, room, status, step_ms, whether audio/face are required) |
| POST | `/sessions/{id}/checkin` | **The core call.** Body below |
| GET | `/sessions/{id}/checkin/me` | My status (`VERIFIED`/`REVIEW`/`REJECTED`/`PENDING`), reasons |
| GET | `/students/me/attendance` | History with per-course percentage |
| POST | `/sessions/{id}/audio-probe` | Optional: upload audio-band SNR numbers when the student opts into room calibration (field-trial mode only) |

`POST /sessions/{id}/checkin` body:
```json
{
  "qr": "gk1:ses_8f2:1766500000:ABCD1234EFGH5678",
  "scanned_at_client_ms": 1766500001234,
  "face": { "vector": [/*128 floats*/], "model": "mfn-v1", "liveness": {"challenge": "turn_left", "passed": true, "frames": 24} },
  "audio": { "heard": true, "code": 1043, "snr_db": 21.4, "band_hz": [18500, 19800] },
  "passkey_assertion": { /* WebAuthn */ },
  "device": { "ua": "...", "platform": "ios|android", "camera_fps": 30, "mic_sample_rate": 48000 },
  "seat_hint": "C-14"
}
```
Responses: `200 {status, score, flags[], latency_ms}` · `410 TOKEN_EXPIRED` · `403 TOKEN_INVALID` · `404 SESSION_NOT_ACTIVE` · `409 ALREADY_CHECKED_IN` · `422 BAD_FACE_VECTOR` · `429 RATE_LIMITED`. Flags are never hidden from the student as text that teaches evasion; the student UI only sees "Verified / Needs professor confirmation / Try again".

### 3.4 Professor session control
| Method | Path | Purpose |
|---|---|---|
| GET | `/courses` · `/courses/{id}/roster` | My courses and enrolled students |
| POST | `/courses/{id}/roster/import` | CSV roster upload |
| POST | `/sessions` | Create/start session: `{course_id, room_id, step_ms, grace_windows, audio_mode: off|tone|fsk, require_face, duration_min}` → `session_id` |
| GET | `/sessions/{id}` | State, counts, config |
| POST | `/sessions/{id}/tokens?from_window=&count=` | Batch of signed QR payloads for local rendering (≤ 120 s ahead) |
| GET | `/sessions/{id}/audio-plan` | FSK schedule (window → code → carrier freqs) for the emitter |
| POST | `/sessions/{id}/pause` · `/resume` · `/close` | Lifecycle. `close` is final and generates the report |
| GET | `/sessions/{id}/attendance` | Live list with status/flags |
| POST | `/sessions/{id}/attendance/{student_id}/resolve` | `{decision: present|absent, note}` for `REVIEW` items |
| POST | `/sessions/{id}/attendance/manual` | Add a student who couldn't scan (reason required, audited) |
| GET | `/sessions/{id}/anomalies` | Mule collisions, bursts, identical-offset clusters |
| GET | `/sessions/{id}/attention` | **Attention Panel** feed: ranked flagged + not-yet-arrived students, each with name, roll no, ID photo URL, reasons, risk score (§11.1) |
| POST | `/sessions/{id}/spotcheck/{student_id}` | Professor's verdict after calling the name: `{result: seen|not_seen|unsure}`. Becomes ground truth (§11.1) |
| GET | `/sessions/{id}/spotcheck/suggestions?k=5` | Top-k students to call out, plus a random audit sample of already-VERIFIED students |
| GET | `/students/{id}/photo?session_id=&sig=` | Short-lived signed ID-photo URL (≤ 5 min), served only to the session's professor, only for students on the Attention Panel; audit-logged |
| GET | `/sessions/{id}/export?format=csv|xlsx` | Export |
| GET | `/courses/{id}/report` | Cross-session attendance summary |

### 3.5 Realtime
`WSS /ws/sessions/{id}?token=` (professor only). Server → client events: `checkin.created`, `checkin.updated`, `anomaly.raised`, `attention.raised` (student flagged or overdue, with signed photo URL), `attention.cleared`, `session.state`, `stats.tick` (count, rate/s, p50/p95 latency every 1 s). Client → server: `ping`. Reconnect with `since_seq` to replay missed events.

### 3.6 Admin / ops and field trials
| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` · `/readyz` · `/metrics` | Health, Prometheus metrics |
| POST | `/admin/rooms` · GET `/admin/rooms` | Room registry: `{name, capacity, type: projector_120|hall_240|tv_room, audio_chain}` |
| POST | `/trial/runs` · GET `/trial/runs/{id}` | Field-trial run (room, device matrix, date); check-ins are tagged with the run |
| POST | `/trial/runs/{id}/observations` | Manual observations (zone, seat, phone model, scan time, outcome) |
| GET | `/trial/runs/{id}/report` | Aggregated per-zone, per-device pass rates |
| DELETE | `/admin/retention/purge` | Enforce retention (face vectors from sessions deleted after N days) |

---

## 4. What each app contains

### 4.1 Student PWA (phone)
1. **Login** (university credentials), **consent screen** (biometric descriptor processing, what is and isn't stored).
2. **Enrollment wizard**: front-camera capture → on-device embedding → upload 128 floats → passkey registration. Retake/delete options.
3. **Home**: today's sessions; big "Check in" button when a session is live.
4. **Scan screen**: rear camera viewfinder (zoom control, torch, "move closer / zoom in" hints), live decode, instant haptic on QR lock.
5. **Verify screen** (≈1.5 s): front-camera face capture with a randomly chosen 1-second liveness prompt, microphone listening indicator, spinner.
6. **Result**: Verified / Needs confirmation / Try again, plus the reason class in plain language.
7. **History**: per-course attendance %, disputes ("I was there") → creates a professor review item.
8. **Settings**: delete my face data, permissions help (camera, mic), language, "audio diagnostics" (shows detected SNR; used in field trials).
9. **Offline/poor network**: queue a check-in for up to the TTL; never silently submit stale tokens.

Not in the student app: roster, other students' data, session controls, thresholds, anomaly details.

### 4.2 Professor console (projector / laptop)
1. **Login → course → "Start session"** (room preset sets QR size/step and audio mode).
2. **Projection view** (fullscreen): giant rotating QR, thin countdown ring, minimal text, optional "scan from the aisle" second QR size; wake-lock; auto-recovers on network blips by using pre-fetched tokens.
3. **Audio emitter control**: start (user gesture), volume meter, "test tone" button, emitter status. Emits FSK schedule from `/audio-plan`. Output device selector.
4. **Attention Panel** (private screen only, never the projected one): students who are flagged or not yet checked in appear **immediately**, as cards with ID photo, name, roll no, plain-language reason, and buttons *Seen / Not here / Unsure*. See §11.1.
4b. **Live dashboard** (second window or tablet): checked-in count / roster, check-ins per second, p95 latency, per-signal pass rates, seat/zone heat map if seat hints are used, anomaly toasts, **Review queue** with one-click Present / Absent.
5. **Manual add** for stragglers; **pause/close**.
6. **Reports**: session summary, CSV/XLSX export, course-level attendance %, flagged students.
7. **Room calibration mode**: plays chirp sweep 16–20 kHz so the trial team can map audio coverage (feeds `/trial/*`).

Not in the professor console: raw face vectors (professor sees flags, not biometrics), student passkeys, global config.

---

## 5. Benchmarks (proper, with sample sizes and CIs)

All results written to `bench/results/<date>/` as CSV and summarised in `docs/RESULTS.md` by a script, never by hand.

### B-PERF: backend load (k6 + Locust)
| ID | Scenario | Pass criteria |
|---|---|---|
| B-PERF-01 | 120 and 240 virtual students, valid check-ins spread over 15 s, then compressed to 5 s (stress) | 0 5xx; P99 ≤ 400 ms (15 s) / ≤ 800 ms (5 s); all rows committed |
| B-PERF-02 | Thundering herd: all 240 within one QR window | Same, no duplicate rows, no lost updates |
| B-PERF-03 | Redis nonce path under contention | per-check ≤ 2 ms avg; memory ≤ 50 MB |
| B-PERF-04 | WS broadcast, 240 events/15 s to 3 dashboards | server-receive → render ≤ 250 ms P95 |
| B-PERF-05 | Soak: 10 sessions × 240 students × 50 min | no memory growth > 10%, no WS disconnect storms |
| B-PERF-06 | Chaos: Redis restart, DB slow (+200 ms), WS drop mid-session | graceful degradation, recovery < 10 s, no data loss |

### B-SEC: security / adversarial (FAR and FRR with 95% CI)
| ID | Attack | Protocol | Min trials | Pass |
|---|---|---|---|---|
| B-SEC-01 | Photo relay (screenshot/photo sent over chat) | Real WhatsApp/Telegram relay, 3 s, 5 s, 10 s delays | 100 | Accepted < 3% at ≥ 3 s delay (upper CI) |
| B-SEC-02 | Replay of captured request | Replay POST after window, and within window by another student | 200 | 100% rejected, no 5xx |
| B-SEC-03 | Forged / tampered token | Fuzzing, wrong session, future windows | 10,000 | 0 accepted |
| B-SEC-04 | Mule (one person, 3 enrolled phones) | Real people × diverse faces | 60 mule sessions | ≥ 95% flagged (TPR) |
| B-SEC-05 | Wrong-person phone (friend scans with your phone) | Face vs. enrolled template | 100 | ≥ 95% flagged |
| B-SEC-06 | Innocent look-alikes / twins / siblings | Genuine different students | 200 pairs | False collision ≤ 1% |
| B-SEC-FACE | Threshold calibration | ROC/DET curve on pilot data across skin tones, lighting, glasses, masks | ≥ 50 people | Report EER and chosen threshold; parity gap between demographic groups ≤ 2× |
| B-SEC-07 | Printed photo / screen replay of enrolled face | With and without active liveness | 100 each | Report spoof acceptance honestly |
| B-SEC-08 | Live video-call relay | Real WhatsApp/Meet call, remote student scans | 50 | **Reported, not claimed 0%.** Quantifies what audio/subnet signals catch |
| B-SEC-09 | API abuse | Rate limits, IDOR (student reads another's data), JWT tampering, SQLi/XSS basics | n/a | 0 findings of high severity |

### B-CLI: client performance (BrowserStack / real devices)
Devices: iPhone 11 or newer (Safari), mid Android (Snapdragon 6/7-series, Chrome), budget Android (3 GB RAM). Targets as in the original suite (FCP ≤ 1.2 s on 4G; model load ≤ 800 ms, cached ≤ 150 ms; embedding inference ≤ 120 ms on mid-tier in-browser (relaxed from 80 ms: the 18 ms paper figure is native); QR decode ≥ 20 fps effective; tab RAM ≤ 150 MB; 10-minute scan session battery ≤ 3%, no thermal warning), plus **time-to-verified from opening the app** (target P50 ≤ 6 s, P95 ≤ 12 s).

### B-ENV: field benchmarks (see §7)
Per room × zone × device: QR decode success and time, audio SNR at 18.5–19.8 kHz, FSK code decode rate, face capture success under room lighting, end-to-end time-to-verified.

**Go / no-go thresholds for production use (decided now, before data):**
- Check-in success without help ≥ 95% overall, ≥ 90% in the worst zone.
- Time-to-verified P95 ≤ 12 s; whole class ≤ 60–90 s.
- Mule/wrong-person TPR ≥ 95%; false flag ≤ 2% of genuine students.
- Audio: if decode rate in a room type is < 90% across zones, that room type runs with audio **off** (score contribution removed) and we say so in the report.

---

## 6. The five phases

Hours are wall-clock from start, assuming Claude Code works near-continuously while you review at phase gates. Each phase ends with a **gate**: automated checks must be green and you sign off before the next begins.

### Phase 1: Foundations + risk spikes (H0–H6)
Goal: kill the three biggest unknowns before building on them.
- Monorepo scaffold, CI (lint, type-check, unit tests), `docker-compose` (FastAPI, Redis, Postgres), OpenAPI skeleton from §3, `shared/` token spec with test vectors.
- **Spike A, audio probe page**: static HTML that emits 16–20 kHz sweep + 19.5 kHz tone + 4-FSK burst; second page listens (getUserMedia with EC/NS/AGC off), shows FFT, SNR at carriers, logs to JSON. Run immediately on your own devices and in one classroom: this gives the first real audio numbers within hours.
- **Spike B, QR decode rig**: canvas QR at various sizes/steps/ECC; decode with zxing-wasm on iPhone + Android at 5/10/20 m, record success and latency.
- **Spike C, face embedding**: ONNX Runtime Web + MobileFaceNet (or face-api.js) in the PWA shell; measure load time, inference ms, and same/different-person distances on ~10 volunteers.
- Deliverable: `docs/SPIKE_RESULTS.md` with numbers, and a **go/adjust decision on audio mode and QR step** recorded in `docs/DECISIONS.md`.
- Gate: spike numbers exist; stack confirmed; CI green.

### Phase 2: Backend core + data model (H6–H16)
- DB models: users, courses, rosters, rooms, sessions, checkins, face_templates, passkeys, anomalies, audit_log, trial_runs, observations. Alembic migrations.
- Token service (HMAC, windows, grace), Redis nonce, session lifecycle, check-in pipeline with trust scorer (config-driven weights), collision engine (in-session pairwise cosine/Euclid with numpy; O(n²) for 240 is trivial), enrollment and match-to-template.
- Auth (JWT), roles, rate limits, idempotency keys, structured logs, metrics, retention job.
- Every endpoint in §3 implemented, with request/response schema validation and the exact error codes.
- Tests: ≥ 90% line coverage on token/scorer/collision; contract tests generated from OpenAPI (Schemathesis).
- Bench: B-PERF-01..03 run locally against the real stack.
- Gate: all §3 endpoints return correct codes; B-PERF-01/03 pass; threat-model doc updated.

### Phase 3: Professor console + student PWA (H16–H30)
- Prof console: login, start session, fullscreen QR with prefetched tokens and local rendering, FSK emitter, dashboard with WS, review queue, export, calibration mode.
- Student PWA: login, consent, enrollment wizard, scan → face/liveness → audio listen → submit → result, history, offline-safe queue, settings/diagnostics.
- Generated typed client from OpenAPI; accessibility and mobile layout pass; i18n scaffold (English + Hindi stub if relevant).
- Unit/component tests (Vitest + Testing Library) and one Playwright smoke test per app.
- Gate: a human can run a full session on localhost with two real phones: professor starts, phones check in, dashboard updates, export works.

### Phase 4: Hardening, anti-abuse, benchmark harness (H30–H40)
- Tune anti-cheat: liveness challenge variants, anomaly clustering (identical latency offset, burst patterns), per-device rate limits, "scanned too fast / too slow" signals, subnet hint.
- Security pass: JWT/IDOR/SQLi checks, secrets handling, TLS config, CSP, dependency audit, privacy review (no images, vectors deleted per retention policy, consent logged).
- Full benchmark harness in `bench/`: k6/Locust scenarios for B-PERF-01..06, adversarial scripts for B-SEC-02/03/09, face threshold calibration notebook, client perf scripts, **field-trial logger** and report generator (`/trial/*`).
- Fault injection: kill Redis/DB, flap network, expire tokens mid-scan.
- Gate: B-PERF all pass locally; B-SEC automated items pass; `RESULTS.md` auto-generates.

### Phase 5: Integration, E2E and release candidate (H40–H48), with Antigravity
Antigravity owns `e2e/` and works from the OpenAPI + `docs/TEST_CONTRACT.md` Claude Code writes in Phase 4. Antigravity should **not** read implementation code first; it tests against the contract so it finds spec/implementation gaps.
- **API integration tests** (pytest + httpx against docker-compose): every endpoint × happy/sad paths × auth roles; idempotency; concurrency (240 parallel check-ins); WS replay after disconnect; clock-skew cases.
- **Browser E2E (Playwright, Chromium + WebKit)**: 1 professor + N simulated students in one run; fake camera/mic injected via `--use-fake-device-for-media-stream`, `--use-file-for-fake-video-capture` (pre-recorded QR video, face videos) and `--use-file-for-fake-audio-capture` (FSK recordings), so scan → face → audio → submit is exercised end-to-end in CI.
- **User-journey tests**: first-time enrollment; check-in; denied camera/mic permission; late scan; double scan; professor resolves REVIEW; manual add; export content matches the DB; delete my face data.
- **Attack scripts**: replay, forged token, cross-student data access, mule vectors (same vector under 3 IDs), expired-window sweep.
- Cross-device matrix on BrowserStack: iOS Safari latest-1, Chrome Android on 3 devices.
- Release candidate: tagged build, seeded demo data, deploy to a public HTTPS host (phones need HTTPS for camera/mic), field kit checklist printed.
- Gate (exit of the 48 h): all integration + E2E green, B-PERF pass, known-issues list written, RC deployed.

---

## 7. Classroom field trials (Day 3 onward)

### Rooms
| Room | Capacity | Display + audio chain | Risk to test |
|---|---|---|---|
| **R1: Projector hall** | 120 | Projector + projector's built-in speaker **or** laptop speakers | Weak/rolled-off tweeter, QR brightness |
| **R2: Big hall** | 240 | Projector/LED wall + PA speakers via HDMI/mixer | PA DSP, limiters, feedback suppressor; distance to back row |
| **R3: Smart classroom** | 40–80 | Large TV with built-in speakers | TV audio processing, speaker cutoff, small QR on TV |

### Protocol per room (2 sessions each: ~25 min per session)
1. **Calibration (10 min, empty or partly full room)**: professor console in calibration mode; testers stand in a 3 × 3 grid (front/mid/back × left/centre/right, plus two worst corners) with the device matrix; the `/trial` logger records SNR across 16–20 kHz sweep, FSK decode, QR decode success/time, face capture OK. Output: heat map per room.
2. **Real class session** with real students (consent obtained, ≥ 60% of capacity if possible): normal check-in; observers log failures by zone/phone.
3. **Attack drills (with willing volunteers)**: photo relay, 3-phone mule, wrong-person phone, one live video-call relay.
4. **Debrief**: compare observed rate vs. §1 estimates; update `DECISIONS.md` per room type: audio on/off/soft, QR step (1.5/3/5 s), QR size, whether a repeater screen is needed.
- Device matrix: ≥ 2 iPhones (Safari), ≥ 3 Android (flagship/mid/budget), at least one with a case-on mic and one old model.
- Ambient noise measured at start (dB meter app), lights up/dimmed.

### Pass/fail mapping
Use the go/no-go thresholds in §5. Expected outcome per room type is likely mixed (e.g. R2 and R3 audio off, R1 audio soft). That is a valid, defensible result, because the product degrades gracefully by design.

---

## 8. Risks, ordered

1. Audio co-location unreliable across speakers and phones. Mitigation: soft score, calibration mode, FSK, honest reporting.
2. 240-seat back-row QR legibility. Mitigation: bigger step, larger QR, repeater screen, aisle scan.
3. Live video relay is not solvable by software alone. Mitigation: layered signals, review queue, deterrence, honest claims.
4. Biometric privacy / compliance (India DPDP Act, GDPR if applicable): explicit consent, delete endpoint, short retention, on-device processing, a documented DPIA; get the university's DPO/administration sign-off before the live class trial.
5. Face-model bias across skin tones and lighting: measured in B-SEC-FACE; if the parity gap exceeds the threshold, widen the review band rather than auto-reject.
6. 48 h scope pressure: cut order if behind schedule: (a) xlsx export, (b) seat heat map, (c) i18n, (d) Postgres (keep SQLite), (e) passkeys. Never cut: token service, face collision/enrollment match, review queue, load/benchmark harness, E2E tests.

---

## 9. Instructions for Claude Code (paste as the kickoff prompt)

> Read `BUILD_PLAN.md` and the product spec in this folder. You are Claude Code, building GateKeeper per §2–§6. Work phase by phase; do not start Phase N+1 until the Phase N gate is green. Obey the spec corrections in §0 (per-student nonce keys; real 128-d face embeddings, not landmark ratios; audio is a soft signal; no "0% FAR" claims). Implement exactly the endpoints, status codes and error codes in §3. After each phase: run all tests, update `docs/DECISIONS.md` and `docs/RESULTS.md`, and commit. Write `docs/TEST_CONTRACT.md` in Phase 4 for Antigravity. If a spike result contradicts an assumption, stop and report to the user with numbers rather than working around it silently.

## 10. Instructions for Antigravity (integration/E2E)

> Work only from `docs/openapi.json`, `docs/TEST_CONTRACT.md` and `BUILD_PLAN.md` §3–§6. Do not read backend/frontend source until your first failing test report is written. Build `e2e/` per Phase 5. Report every contract violation as: endpoint, request, expected (per contract), actual, severity. Deliver a pass/fail matrix across browsers/devices and the attack-script results.

---

## 11. Addendum: Attention Panel, hardening, latency, zero-manual

### 11.1 Attention Panel (flag immediately, with photo, for human cross-check)
**What it is.** The moment a student is flagged, or is still missing after the grace period, a card appears on the professor's private screen: ID photo, name, roll number, reason, risk score. The professor calls out 3–5 names, looks, and taps *Seen / Not here / Unsure*. We never auto-label anyone "absent" for being flagged; flagged means "worth a 5-second look".

**Who goes on it** (ranked by risk, capped at k=5 visible at a time so it stays low-effort):
1. Checked in but flagged: face collision, face ≠ enrolled template, liveness failed, off-campus-network + no audio, device shared across accounts.
2. Not checked in after `grace_minutes` (default 3), ordered by past-attendance risk.
3. A small **random audit sample (2–3) of VERIFIED students**. This is how we measure false-accept rate in real life: if the professor says "not here" for a VERIFIED student, that is a confirmed cheat that got through.

**Photo source.** The university's ID-card/SIS photo, not a camera capture from the session. This keeps "no photos captured or uploaded by the check-in flow" true, and the same photo is used to bind enrollment (a friend cannot enroll their own face under your account).

**Privacy rules (must-haves, not nice-to-haves):**
- Shown only on the professor's private view; the projector view has no student data at all. Console refuses to render the panel when it detects it is the projected/fullscreen-mirrored window (the QR window is a separate route).
- Photos load via signed URLs valid ≤ 5 min, only for students currently on the panel, no download, no caching, no whole-roster photo grid. Every view is audit-logged (who, which student, when).
- Wording is neutral: "Please verify in person", never "cheating" or "absent", and nothing is recorded as a violation from a flag alone.
- Disputes: the student's history shows "verified by professor" outcomes; a *Not here* verdict on a student who says they were present creates a dispute item for the administration, not the professor.
- Legal: lawful basis and notice for using ID photos for attendance; confirm with the university's data-protection officer before the live trial (India DPDP Act).

**Ground truth loop.** Every spot-check verdict is stored with the signals that produced the flag. Benchmarks use them: FAR from audit-sample "not here" rates, flag precision (how often a flagged student really was absent), and threshold re-tuning.

**Tests.** E2E: flagged student appears on panel in ≤ 500 ms from check-in; photo URL expires; a different professor gets 403; panel never renders in the projection route; spot-check verdict updates attendance and audit log; audit sample excludes already-called students.

### 11.2 Extra anti-cheat signals added to Phases 2–4
| Item | Phase | Notes |
|---|---|---|
| Enrollment bound to ID photo (server compares enrollment embedding to SIS photo, or supervised enrollment in week 1) | 2 | Closes the "friend enrolls as you" hole |
| One-time server challenge per scan, included in the signed payload | 2 | Kills replay of captured requests |
| Passkey signature over (token, challenge, SHA-256 of face vector); one device per account, device change = re-enroll | 2–3 | Stops scripts/laptops/AI agents posting check-ins |
| Colour-flash liveness (random colour sequence on phone screen, front camera must see it on the face) | 3 | Beats recorded and injected video |
| Random mid-class re-checks (2–3 per lecture, 30 s window, push to phone) | 3 | Catches check-in-and-leave and relay handoff |
| Server-side behaviour analytics + per-student term risk score | 4 | Same latency offsets, shared device/IP, always first/last scan, impossible class-to-class movement |
| Campus network signal: AP/room association from university IT (optional input) | 4 | Strongest anti-relay signal; server-side, unspoofable by client |
| Motion-sensor vs camera-motion consistency; virtual-camera/emulator hints | 4 | Soft signals on web; real attestation needs native (v2) |
| Uniform error messages, thresholds and weights server-only, per-build client obfuscation | 2–4 | Speed bump only; security must not depend on secrecy |
| Red-team drill with trusted students during field trial | Field | Findings feed `DECISIONS.md` |

Version 2: native Android/iOS app (Play Integrity / App Attest, raw mic for reliable ultrasonic audio); optional server-side face check for ephemeral in-memory processing.

### 11.3 Latency budget (replaces the earlier 6 s / 12 s targets)
Preload app + face model + camera + `/time` + connection on app open; run face capture in parallel with QR scanning; int8 quantized embedding on WASM SIMD/WebGPU; one request, stateless HMAC verify, Redis only on the critical path, DB write after the response; campus/in-region host with HTTP/3.
Targets: QR decode ≤ 500 ms, embedding ≤ 100 ms, server ≤ 80 ms, **tap-to-verified P50 ≈ 3 s, P95 ≤ 6 s**, Attention Panel latency ≤ 500 ms. B-CLI and B-PERF thresholds updated accordingly.

### 11.4 Zero-manual workflow
Sessions auto-start/close from the timetable; each room has a fixed console URL on its projector/TV; roster sync from SIS and result push to LMS/ERP; flagged students get an automatic re-challenge on their phone within 60 s before they ever reach the professor's panel (only unresolved ones appear); repeat offenders escalate to administration. Professor effort per class: glance at the panel, call at most ~5 names. One-time manual step: supervised/ID-bound enrollment.

---

## 12. Decisions locked by the grill-me interview (2026-10-24)
These override earlier sections where they conflict.

| # | Decision | Consequence for the build |
|---|---|---|
| 1 | **Professor-only pilot.** No university SIS, ID photos, or IT/Wi-Fi access | Drop AP/room Wi-Fi signal (§11.2); roster comes from professor CSV upload; no SIS/LMS sync in v1 |
| 2 | **Photo source = in-class enrollment with ID check.** Student takes a reference selfie in the app; professor/TA looks at their ID card and taps "confirmed" | Reverses "no photos stored": store **one reference photo per student, encrypted at rest, with explicit consent, deleted at term end**. Session check-ins still upload only the 128 numbers, never images. Add `enrollment_confirmed_by`, `confirmed_at` fields and a professor enrollment-confirm screen. Attention Panel serves this photo (§11.1 rules still apply). The "zero photos stored" pitch line must change to "no photos leave the phone during check-in" |
| 3 | **Threat level v1 = casual cheaters** (forwarded QR, photo relay, friend scanning, 2–3 phone mule) | PWA stays. Native app + hardware attestation and emulator/virtual-camera defences are v2, decided after a semester of real data |
| 4 | **Professor verdict is final and appealable** | Nothing automatic ever marks a student absent. Student can dispute in-app; dispute goes to the professor next class with the signal log attached. Add `disputes` table and `/sessions/{id}/disputes` endpoints |
| 5 | **No smartphone / dead battery / consent refused → professor manual add with reason** | Face is optional-by-consent; consent-refusing and manually added students always appear on the spot-check list; manual adds are audited |
| 6 | **Hosting = own small cloud VM, India region, private repo, HTTPS, you hold the keys** | Docker-compose on one VM; Redis + Postgres on the same box; nightly encrypted backup; no third-party analytics SDKs |

### Open questions still to settle (not yet answered)
- Is the reference selfie retention "end of term" acceptable to your professor/institution, and who is the named data owner (needed for DPDP notice)?
- What do we do if a class has more than ~5% of students unable to enroll in week 1?
- Who approves running the pilot in other professors' classrooms (the 120/240/TV rooms)?

### 12.1 Outside-the-room presence (added 2026-10-24, no Wi-Fi available)
Layers: QR line-of-sight (size/placement chosen so it is unreadable through the door), acoustic burst, **peer-proximity chirp graph** (each phone emits a short ultrasonic ID at check-in and listens; server flags phones nobody inside hears; Phase 4 experiment), coarse GPS geofence (professor device registers room location, ~100 m), mid-class re-checks, spot-check panel, door-closed policy.
New benchmark **B-SEC-10**: student scans from outside, door closed / open / glass wall, at 1 m, 3 m, 10 m; 30 trials per condition; report acceptance rate with 95% CI per layer and combined. Estimate before data: ~90% blocked behind a closed door, ~60% with an open door or glass.
Phase 1 status: token service (6 tests), API skeleton (2 tests), audio and QR spike pages in `spikes/` built and unit-tested where possible; spike pages not yet run on real devices.
