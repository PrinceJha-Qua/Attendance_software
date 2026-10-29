# Field trial protocol

Purpose: replace every "chosen from literature" number in GateKeeper with a measured one, in real rooms, with real phones, before it is relied on. Nothing here changes code by itself: results go into `docs/DECISIONS.md` and, if a threshold moves, into `backend/app/config.py` and `trust.py` in a new release.

Reference: targets and benchmark ids come from `docs/design/BUILD_PLAN.md` §5 and §7; the audio reasoning is in `docs/research/ultrasonic-audio-reliability.md`.

## 0. Before anyone walks into a room

- [ ] Institution has signed off the data owner and the consent text (DPDP). Students see and accept the consent in the app; trial participation is voluntary and the alternative (paper roll call) is offered.
- [ ] Backend deployed per `docs/DEPLOY.md`, `GET /api/v1/readyz` green, HTTPS valid on both hostnames.
- [ ] `GK_TRIAL_MODE=1` **only** if audio is being measured this session. Turn it off afterwards.
- [ ] Professor account created; course and roster imported; initial passwords handed out.
- [ ] Device matrix ready: at least 2 iPhones (Safari), 3 Android (flagship, mid, budget) in Chrome; one phone with a case on the mic and one old model.
- [ ] Spare phone as a hotspot; paper roll call as fallback; a dB-meter app; a notebook (or the trial sheet below).
- [ ] Projector/TV test: open `Projection` on the actual display, press F, check the QR is readable from the back row at the chosen size.

## 1. Enrollment drill (one per cohort, 15 minutes)

1. Students log in, accept consent, take the selfie.
2. Professor confirms each against the ID card.
3. Record: number who could not enroll (target < 5%; if over, plan an alternative route before any real class), time per student, retake reasons (light, glasses, mask, camera).

## 2. Per-room calibration (10 minutes, room mostly empty)

Rooms to cover: R1 projector hall (about 120), R2 big hall with PA (about 240, if available), R3 smart classroom / TV (40–80).

1. Testers stand in a 3 × 3 grid (front/middle/back × left/centre/right) plus the two worst corners, each with the device matrix.
2. Professor starts a session with the beacon on; Projection fullscreen; volume at the default, then one step up and down.
3. At each position, each tester runs a check-in. Record: QR decoded (yes / seconds), face captured (yes / retries), audio status shown in the trial log (`heard`, `unavailable`, `mismatch`) and its level, final status.
4. Note ambient noise (dB) and lighting (lights on / dimmed).

Output: a heat map per room of QR success, audio decode, and time-to-verified.

## 3. Real class sessions (2 per room, about 25 minutes each)

- Normal class with consenting students, at least 60% of capacity if possible.
- Observers log every failure by seat zone and phone model: what the student saw, what the console showed.
- Professor uses the Attention Panel exactly as intended and records each *Seen / Not here / Unsure* verdict.
- The two random audit samples per session are always checked; they are the real false-accept measurement.

## 4. Cheating drills (willing volunteers only)

Each drill is run by volunteers who tell the professor beforehand which accounts are involved. Log the check-in status and flags for every attempt.

| Drill | What the volunteers do | Minimum attempts | Want |
|---|---|---|---|
| Photo relay | A in the room photographs the QR and sends it by chat to B outside; B tries after 3 s, 5 s, 10 s | 100 | Accepted < 3% at ≥ 3 s |
| Request replay | Capture a check-in request and resend it (same student, other student, later window) | 200 | 100% rejected, no 5xx |
| Wrong-person phone | A scans with B's phone and B's login | 100 | ≥ 95% flagged (`FACE_MISMATCH`/`LIVENESS_*`) |
| Mule | One person checks in three enrolled accounts with three phones | 60 sessions | ≥ 95% flagged (`FACE_COLLISION`/`FACE_MISMATCH`) |
| Screen / print replay | Show a photo or video of the enrolled face to the front camera | 100 each | Report acceptance honestly |
| Live relay | Remote student on a video call with the QR visible scans from home | 50 | **Reported, not claimed 0%**: shows what audio and spot-checks catch |
| Look-alikes | Siblings / similar-looking students in one session | 200 pairs | False collision ≤ 1% |

## 5. What to export afterwards

- Check-ins: status, score, flags, `evidence` (face distance, liveness cosines, collisions), `latency_ms`, device info, per student per session (from the database; vectors are not needed).
- Audit sample verdicts and Attention Panel verdicts (table `spotchecks`).
- Audio telemetry (table `audio_logs`, only exists if trial mode was on).
- Disputes raised after the session and how the professor decided.
- Trial sheet: room, zone, phone, ambient dB, lighting, notes.

## 6. Decisions to make from the data

Fill this table in `docs/DECISIONS.md` after each room type.

| Question | Evidence | Rule |
|---|---|---|
| Is the face threshold `0.55` right? | Distances of genuine vs. wrong-person attempts; look-alike collisions | Pick the threshold at the point where false flags on genuine students ≤ 2% and mule/wrong-person TPR ≥ 95%. Report equal-error rate and the gap across skin tones / glasses / lighting (parity gap ≤ 2×) |
| Is collision `0.45` right? | Look-alike pairs | False collision ≤ 1% |
| Liveness cosine `0.3`, 3 of 4 | Real faces under the room's lighting vs. screen/print replay | Real-face pass ≥ 97%; report replay acceptance |
| Audio on, soft or off for this room type? | Decode rate across zones | Decode < 90% across zones → audio **off** for that room type (we say so); ≥ 90% → keep as soft signal |
| QR step (1.5 / 3 / 5 s) and size | QR decode success and time at the back row | Pick the fastest step that keeps worst-zone success ≥ 90% |
| Ready for real classes? | Whole trial | Check-in success without help ≥ 95% overall and ≥ 90% in the worst zone; time-to-verified p95 ≤ 12 s; whole class in 60–90 s; false flag ≤ 2% of genuine students |

A mixed result is a valid result: for example audio off in the big hall, soft in the projector hall. The product degrades gracefully by design.

## 7. After the trial

- [ ] Set `GK_TRIAL_MODE=0` and restart the backend.
- [ ] Run `scripts/retention.py` (and `--term-end` for trial courses).
- [ ] Write the results and chosen thresholds into `docs/DECISIONS.md`; open items into `docs/KNOWN_ISSUES.md`.
- [ ] Thank the students; tell them how to delete their face data.
