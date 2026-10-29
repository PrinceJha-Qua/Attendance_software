# Phase 1 spikes (run on real devices)
Phones need HTTPS for camera/mic. From this folder: `python3 -m http.server 8000` then expose it, e.g. `npx cloudflared tunnel --url http://localhost:8000` (or any HTTPS tunnel), and open the https URL on each device.

1. **Audio**: professor laptop opens `audio_emitter.html`; phones open `audio_listener.html`, type phone + zone, start listening. Press emitter buttons 1-3 in turn (~20 s each). Download the log from each phone. Judge: SNR at 19.5 kHz >= 18 dB = pass; compare with the 16 kHz level to see the phone's mic roll-off.
2. **QR**: laptop/projector opens `qr_show.html` (try step 1500/3000, ECC M/Q, size 40-100%). Phones open `qr_scan.html` at 5/10/20 m. Check decodes, fps and decode_ms.
3. Record numbers in `docs/SPIKE_RESULTS.md`; decisions go in `docs/DECISIONS.md`.
Face-embedding spike (C) is the next Phase 1 task.
