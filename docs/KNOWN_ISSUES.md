# Known issues and open items (v1.0.0-rc1)

## Not proven (needs the field trials)
- Face thresholds (0.55 / 0.45), liveness (cosine 0.3, 3 of 4), verified score 0.8: chosen from the literature, never calibrated on real students. Fairness across skin tones, glasses and lighting is unmeasured.
- Ultrasonic audio: codec verified in simulation (`docs/audio_sim_results.txt`); real phone microphones, browser audio processing and room speakers are untested. It is a soft signal for that reason.
- QR decode rate and speed on real phones at the back of a real room.
- Throughput: the 240-student burst was exercised by the acceptance suite on SQLite, not on the production stack.

## Not verified
- PostgreSQL: all automated runs used SQLite.
- `deploy/` (Docker compose, Caddy, backup script): written without Docker available; first deployment is a dry run.
- Cross-browser UI: Chrome Android and Safari iOS regression has not been run on real devices.

## Product gaps
- **No void-session action**: a session closed by mistake with nobody checked in counts everyone absent (fix by disputes or manual resolve).
- **No schema migrations**: tables come from `create_all`. Any column change after launch needs a written migration first.
- **Dispute race**: two simultaneous disputes on one record could both be stored (no unique constraint yet).
- **Disputes have no deadline and no notifications.**
- **Single backend worker**: the live feed hub is in-process. Scale the VM, not workers.
- **Access tokens cannot be revoked** (stateless JWT, 1 hour). Logout only clears the device.
- **Live relay of the QR from inside the room's video call** is mitigated, not stopped. No software-only web app can fully stop it.
- **Enrollment gap**: if more than 5% of students cannot enroll (camera, face model), there is no alternative flow yet.
- Professor app main bundle is 211 KB (69 KB gzipped); the 200 KB budget was meant for the student app (170 KB).

## Institutional (not code)
- Named data owner and DPDP sign-off (consent text, lawful purpose, grievance contact).
- Approval for classrooms beyond the pilot professor.
- Deployment target: VM, domain, access list for who can reach face data.

Ideas not built are in `docs/PARKING_LOT.md`.
