# Near-ultrasonic (17-20 kHz) data-over-sound design for classroom attendance (Web Audio, browser-only receiver)

Method note: ~10 searches, snippet-level evidence mostly; PDFs of primary papers were not parsed (SonicPACT PDF unreadable). Items labelled Inferences are my own reasoning/calculation, not sourced.

## 1. Modulation schemes in practice and reported reliability

### Takeaway
Production-grade near-ultrasound systems trade bitrate for robustness: Google's Nearby protocol uses 127-chip direct-sequence spread spectrum at only ~94.5 b/s in 18.5-20 kHz, reliable at 2 m and often 10 m. Chirp-based (CSS) academic systems reach tens of metres at 20-125 bps. For attendance (a token of a few bytes), a low-rate spread-spectrum/chirp design is the right family; high-rate OFDM/PSK is unnecessary.

### Cited Findings
- Google's near-ultrasonic protocol (Getreuer, Gnegy, Lyon, Saurous, IEEE Trans. Multimedia 2018): 18.5-20 kHz band, commodity phone speakers/mics, part of Google Nearby, sends short tokens to establish co-presence; DSSS with 127-chip PN code; raw rate 94.5 b/s; reliable at ~2 m, often works at 10 m indoors; walls block it so devices are effectively same-room — [Academia copy](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- Google patents describe near-ultrasound pairing and calibrating receiver audio pipelines because manufacturers' audio preprocessing distorts the signal — [US12119884](https://patents.google.com/patent/US12119884)
- Laptop-oriented high-rate work: coherent modulation + adaptive equalisation in 18-20 kHz reaches 4 kbps up to 5 m — [arXiv 2103.11261](https://arxiv.org/pdf/2103.11261)
- Surrey/XJTLU chirp work: chirp-BPSK with orthogonal up/down chirps; earlier chirp system error-free to 20 m at 125 bps; Shi et al. 2023 (Physical Communication 60) CSS + fractional Fourier denoising, error-free 70 m at 20 bps (Android) — [Shi 2023 PDF](https://personalpages.surrey.ac.uk/w.wang/papers/Shi%20et%20al_PhysicalCommunication_2023.pdf), [XJTLU record](https://scholar.xjtlu.edu.cn/en/publications/a-long-range-aerial-acoustic-communication-scheme/), [NTU record](https://irep.ntu.ac.uk/id/eprint/49564)
- Near-ultrasonic time-reversal chirp modulation: error-free up to 40 m (abstract-level only) — [HAL record](https://hal.univ-lyon2.fr/IL/hal-05413229v1)
- ggwave (open source, has JS/WASM build): multi-tone FSK, 4-bit chunks, 6 tones in parallel over ~4.5 kHz, 96 tones spaced 46.875 Hz, ultrasonic base freq F0=15000 Hz (so ~15-19.5 kHz), Reed-Solomon ECC, start/end markers, FFT receiver; 8-16 bytes/s — [ggwave README](https://cdn.jsdelivr.net/gh/ggerganov/ggwave@master/README.md), [PyPI](https://pypi.org/project/ggwave/)
- Chirp.io (CTO interview): acoustic channel supports hundreds of bps, fastest ~2.5 kb/s only short-range, tens of bps at long range; range is set by playback loudness — [CircuitDigest](https://circuitdigest.com/interview/dan-jones-cto-of-chirp-explains-how-they-transmit-data-over-sound-using-ordinary-speaker-and-microphone)
- Lisnr: reported ~300 bps commercial, 1-3 kbps in beta (press) — [Digital Trends](https://www.digitaltrends.com/cool-tech/lisnr-transfer-files-audio/), [TechCrunch](https://techcrunch.com/2017/05/16/lisnrs-data-over-audio-technology-aims-to-replace-qr-code-scanning-nfc/)
- Vendor reliability claims (Chirp, Lisnr) are marketing; I found no independent packet-success-rate data for Chirp, Lisnr, SilverPush, CourseKey. Sonarax: nothing found.

### Inferences
- Attendance payload (e.g. 32-bit session code + 8-16 bit CRC/FEC ≈ 6-8 bytes) at ~50-100 b/s ≈ 1-2 s per burst, in line with Google's 94.5 b/s DSSS design. This is the sweet spot: long enough for ~20+ dB processing gain, short enough to repeat many times.
- Pure single tone is trivially jammed by narrowband noise (HVAC, electronics, whistles) and gives no payload binding; MFSK (ggwave-style) is simple and JS-proven but has lower processing gain than DSSS/CSS; DSSS or chirp with matched filtering is preferable. OFDM/PSK need equalisation and are overkill/fragile with phone speaker nonlinearity.
- A hybrid is practical: a linear-chirp preamble (sync + channel check) followed by a DSSS or chirp-CSS payload.

### Gaps
- No peer-reviewed packet-success-rate numbers in real classrooms with 20-40 phones; no measured data for SilverPush/CourseKey; Google paper figures are from an abstract/mirror, not read in full.

## 2. Detection, coding, diversity, and quantifying detection probability

### Takeaway
Detect with a matched filter (cross-correlation against a known chirp/PN preamble) using an adaptive noise-floor/CFAR threshold, then decode with FEC; repeat bursts in time and use 2+ frequency slots. Detection probability can be computed from correlation SNR and verified empirically.

### Cited Findings
- Google protocol's DSSS (127-chip) is stated to be robust to multipath, motion and narrowband noise — [Academia copy](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- ggwave receiver: finds start/end markers, FFT, tone mapping, Reed-Solomon decode — [ggwave README](https://cdn.jsdelivr.net/gh/ggerganov/ggwave@master/README.md)
- Chirp-based schemes use orthogonal up and down chirps to spread band and increase reliability — [Shi 2020/2023 papers](https://personalpages.surrey.ac.uk/w.wang/papers/ShiYHZWLWW_CollaborateCom_2020.pdf)
- Android CDD near-ultrasound requirements (devices that advertise support): mic mean response in 18.5-20 kHz no more than 15 dB below 2 kHz; speaker no more than 40 dB below 2 kHz in that band — [Android CDD note](https://android.googlesource.com/platform/docs/source.android.com/+/20cdbb7%5E%21)

### Inferences (design maths, unsourced)
- Processing gain of correlation = 10*log10(TB). A 100 ms chirp sweeping 17.5-19.5 kHz (B=2 kHz) gives TB=200, about 23 dB; a 300 ms burst ~28 dB. Because speaker/mic roll-off at 19-20 kHz can cost 20-40 dB, that gain is what is needed.
- Detection probability: for a known-waveform matched filter in AWGN, Pd = Q(Q^-1(Pfa) - sqrt(2E/N0)). Choose Pfa per listening window (e.g. 1e-4 over a 60 s window with ~N correlation lags) via CFAR: threshold = k * median/MAD of correlation magnitude over a rolling noise window. Fading/multipath: validate empirically rather than trusting AWGN.
- Peak-to-sidelobe ratio and a second check (e.g. expected inter-burst spacing, CRC) cut false positives further; require 2 of N bursts with the same payload for the session code.
- Time diversity: if per-burst success is p and bursts are roughly independent (not true if the phone is simply deaf at that band), N bursts give 1-(1-p)^N; with p=0.8 and N=5 -> 99.97%. Correlated failures (a device that cannot hear 19 kHz, a blocked mic, a pocket) are the real limit, hence frequency diversity and calibration.
- Frequency diversity: 2-3 slots (e.g. ~17.5, ~18.5, ~19.5 kHz centres, or a chirp that sweeps the full band so any usable sub-band contributes). Interleave slots across repeats.
- FEC: for ~8-byte payloads use CRC-16 + convolutional or RS(255-style shortened) with a few parity bytes; repetition of the whole burst is cheaper than heavy FEC.

### Gaps
- No sourced CFAR or Pd-vs-SNR curves for acoustic classroom channels; would need own measurement.

## 3. Web Audio implementation constraints

### Takeaway
Use AudioWorklet (audio-thread DSP) for the receiver, getUserMedia with echoCancellation/noiseSuppression/autoGainControl off, request 48 kHz, verify actual AudioContext.sampleRate, and verify the track really passes >16 kHz, since browser/OS processing can destroy the band.

### Cited Findings
- ScriptProcessorNode is deprecated (main thread, async, latency, UI jank/glitches); AudioWorklet runs on the audio rendering thread with no extra latency, processing 128-sample blocks — [Chrome blog](https://developer.chrome.com/blog/audio-worklet)
- Posting messages from worklet to main thread had anecdotal 2-5 ms delay (single GitHub issue, not a benchmark) — [waaclock #20](https://github.com/sebpiq/waaclock/issues/20)
- Disabling echoCancellation alone also disables NS/AGC in Firefox tests; per-constraint support varies — [Firefox test](https://arai.searchfox.org/firefox-main/source/dom/media/webrtc/tests/mochitests/test_defaultAudioConstraints.html), [MDN getCapabilities](https://developer.mozilla.org/en-US/docs/Web/API/MediaStreamTrack/getCapabilities)
- Historic issue: Safari 11 attenuated >~12 kHz even with the three flags off (WebKit bug 179411, reported fixed; current behaviour unconfirmed); W3C thread notes Chrome applied a hidden high-pass filter — [WebKit bug](https://bugs.webkit.org/show_bug.cgi?id=179411), [W3C log](https://lists.w3.org/Archives/Public/public-media-capture-logs/2017Jun/0118.html)
- Android near-ultrasound spec above; iPhone 7/8 loopback measurements: sharp drop above 20 kHz, iPhone 8 attenuation from ~19 kHz, so SonicPACT used 17-19 kHz carriers — [SonicPACT arXiv 2012.04770](https://arxiv.org/pdf/2012.04770) (via search summary; PDF not parsed)
- Patent text: many consumer speakers/mics roll off above 19-20 kHz — [US 12207904](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12207904)

### Inferences
- Do not rely on AnalyserNode for detection: its FFT frames are unsynchronised polling on the main thread (jitter under load), no phase, can miss samples; fine for a level/spectrum UI and for the self-test display only. AudioWorklet should do Goertzel/correlation on a decimated or direct stream and post only detection events.
- Clock drift: emitter and receiver oscillators differ by tens of ppm; over a 1-2 s burst at 18 kHz that is ~1 Hz and sub-sample timing drift, negligible for chirps/DSSS of <=2 s; use the preamble for timing, and a small bank of Doppler/offset hypotheses (±0.5%) cheaply covers phone motion. 44.1 vs 48 kHz: generate waveforms parametrically from the receiver's actual sampleRate; Nyquist at 44.1 is 22.05 kHz, so 20 kHz is feasible but the anti-alias roll-off is steeper, favour <=19.5 kHz. Resampling by the browser can add images; prefer native rate.
- CPU/battery: a 128-sample-block correlator against 1-3 templates via FFT-based or decimated (downmix to baseband, decimate by ~4-8) correlation is light; run listening only within the check-in window to save battery; avoid main-thread work.
- iOS Safari needs a user gesture to start the AudioContext/mic; PWA mic permission persistence varies. (General knowledge, not verified here.)

### Gaps
- Current (2026) browser behaviour of raw mic bandwidth on iOS Safari/Chrome Android not verified; needs device-lab test. No sourced AudioWorklet CPU benchmarks on low-end phones.

## 4. Capability negotiation and tiered bands

### Takeaway
Run a short self-test that plays/hears a known chirp and measures SNR per band; choose a tier from measured results rather than device model. Lower bands improve coverage but cost audibility.

### Cited Findings
- Device responses roll off from ~19-20 kHz (iPhone 8 from ~19 kHz; patent says many roll off 19-20 kHz); Android near-ultrasound-certified devices must be within 15 dB (mic) of 2 kHz in 18.5-20 kHz — sources in Section 3.
- Patent: 17/18-24 kHz generally inaudible to most people — [US 11707197](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11707197). ggwave's ultrasonic protocols start at 15 kHz, i.e. accept some audibility — [README](https://cdn.jsdelivr.net/gh/ggerganov/ggwave@master/README.md)
- Google's patents mention calibrating receiver audio pipelines — [US12119884](https://patents.google.com/patent/US12119884)

### Inferences
- Tiers: T1 18-19.5 kHz chirp/DSSS; T2 16-18 kHz (audible to some teenagers/young adults, mostly faint); T3 non-acoustic fallback (QR/code on screen, BLE not available in Safari; manual code) for the few devices failing both. Never rely on the sounder being the only path if 99% is required.
- Self-test: receiver plays nothing; teacher/emitter device emits calibration chirps per band at known level at session start or a "test" button; student app reports per-band matched-filter SNR and whether the mic path passes >16 kHz; log tier to backend. Also self-loopback (own speaker -> own mic) tests the student's mic path but not the emitter's.
- Audible spread-spectrum "hidden under noise" (e.g. low-level broadband PN under classroom ambient): gives band-independent coverage but needs far longer integration or louder level; audibility cost is unquantified here. No source found measuring this, so treat as a research option for T3 not v1.

### Gaps
- No sourced numbers on the share of phones that can hear 18/19 kHz in current fleets; no audibility thresholds by age beyond the patent's general statement.

## 5. Statistics for a defensible 99% claim

### Takeaway
Zero-failure demonstration needs ~299 independent successes for 99% at one-sided 95% confidence; stratify by device/room and define reliability as per-check-in success within N attempts, not per burst.

### Cited Findings
- Rule of three: zero failures in n gives 95% upper bound on failure ~3/n; 300 trials supports >=99% — [Quality Digest](https://www.qualitydigest.com/inside/statistics-column/rules-three-and-five-082317.html), [Pearson](https://www.pearson.com/channels/statistics/asset/93c26b57/no-failures-according-to-the-rule-of-three-when-we-have-a-sample-size-n-with-x0-)
- Zero-event sample sizes for failure bound 0.01: ~299-300 at 95%, ~461 at 99% confidence (range quoted in a clinical biostatistics paper) — [Turkiye Klinikleri](https://turkiyeklinikleri.com/article/en-tedavide-basarisizlik-olasiligi-icin-ust-sinir-belirleme-yontemlerinin-karsilastirilmasi-metodolojik-bir-calisma-109311.html)
- Exact: 0.99^n <= 0.05 -> n >= 299 (calculation reported in search output, consistent with rule of three).

### Inferences
- With f failures allowed, use Clopper-Pearson lower bound on success; e.g. 1 failure needs ~473 trials, 2 failures ~628 for 99% at 95% (my standard binomial calculations; verify with a script before publishing).
- Define the unit: a check-in = a student's session with up to N bursts/attempts (e.g. 3 repeats over 30-60 s window) plus T3 fallback; claim "P(check-in succeeds within window) >= 99%". Per-burst p of 0.8-0.9 with independent repeats suffices, but verify independence empirically; failures cluster by device/seat.
- Stratify: test per device model/OS/browser (at least a matrix of ~10-20 representative models), per seat distance/angle, per noise condition. A pooled 300 can hide a 100% failure on one 3%-share device; either require per-stratum minimums or report a worst-stratum bound and weight by fleet share (and use the tier/fallback to cover known-deaf models).
- Keep false-accept (spoofing/leakage to neighbouring room, replay) separate from detection probability; use rotating session codes.

### Gaps
- Real classroom reliability data do not exist in the sources found; the 99% figure must be earned by own field trials.

## Recommended concrete design (synthesis, inferences)
1. Emitter (teacher laptop/phone): every ~3 s, a 1.2-1.5 s burst: 100 ms up-chirp preamble spanning 17.5-19.5 kHz (sync), then payload of 64-bit frame (session ID + counter + CRC-16) via DSSS/CSS at ~50-90 b/s, convolutional/short RS FEC; rotate a second band slot (~17-18 kHz) on alternate bursts. Raised-cosine edges to avoid clicks. Level at ~-12 dBFS to avoid speaker clipping/intermodulation audibility.
2. Receiver (PWA): getUserMedia with all processing off, AudioWorklet, template cross-correlation via decimated baseband, adaptive MAD-based CFAR threshold, soft-combine repeated bursts across time, accept on CRC + 2-of-N consistency.
3. Self-test at join: per-band SNR measured against calibration chirps; choose tier; show student a live "hearing" indicator; fall back to T3 if T1/T2 SNR < threshold.
4. Validation: lab matrix + classroom trials, >=300 check-ins with 0 failures (or ~470 with 1) overall plus per-stratum minimums; report Clopper-Pearson bounds.
