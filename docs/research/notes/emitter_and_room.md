# Emitter and room path: delivering 17-20 kHz sound to students up to 30 m

Method note: web search in this session was weak on primary hardware data. Many questions (per-device >16 kHz response, feedback suppressor behaviour, TV enhancement modes, wall/door TL at 19 kHz, HDMI/OS behaviour) returned NO citable primary source and are listed under Gaps. Air-absorption numbers below were computed by the researcher from the ISO 9613-1:1993 pure-tone formula (as reproduced at sengpielaudio) and are labelled as such. Everything under "Inferences" is reasoning, not sourced fact.

## 1. Emitter hardware: frequency response above 16 kHz (laptops, projectors, TVs, PA, HDMI/Bluetooth/USB)

### Takeaway
No authoritative measurement of >16 kHz output for MacBooks, Windows laptops, projector speakers, TVs or PA was found. The little evidence says laptop speakers are non-flat and roll off sharply near 14 kHz yet can still radiate a 18-20 kHz tone at usable level (~63-64 dB SPL at 20 cm, 100% volume in one study). Per-room, per-emitter measurement is unavoidable.

### Cited Findings
- Laptop-to-laptop 18-20 kHz link using built-in speakers/mics: hardware response in that band is "usually nonflat"; measured signal at 20 cm, 100% volume: 64.1 dB(Z) at 18 kHz, 64.3 at 19 kHz, 62.9 at 20 kHz; quiet-lab noise floor 48.8 dB(Z) — [Ultrasonic comm. with consumer devices, arXiv 2103.11261 (ar5iv)](https://ar5iv.labs.arxiv.org/html/2103.11261)
- Same study BER vs distance (laptops, QPSK 19 kHz carrier): 0.5 m and 2 m <2e-4 BER; 3 m 0.08 in one direction; 5 m at 65-80% volume ~0.1 BER, at 100% volume <2e-4 / 0.01. Received power was lower at the device sides than the front (orientation dependence) — [arXiv 2103.11261](https://ar5iv.labs.arxiv.org/html/2103.11261)
- Informal, anecdotal: a Hacker News commenter measured a MacBook Pro with sine waves; it emitted/picked up 20 kHz but response "fell off sharply around 14 kHz" with some distortion (uncontrolled; not a measurement-site source) — [Hacker News thread](https://news.ycombinator.com/item?id=6647590)
- Notebookcheck laptop speaker measurements only cover a "highs" band of 2-16 kHz (MacBook Pro 15 2017 ~87 dB loudness), so they do not reveal 17-20 kHz behaviour — [Notebookcheck CN, MBP 15 2017](https://www.notebookcheck-cn.com/Apple-MacBook-Pro-15-2017-2-8-GHz-555.231356.0.html)
- RTINGS TV frequency-response test sweeps 10 Hz-22 kHz at 48 kHz/16-bit (-6 dBFS), reported 20 Hz-20 kHz at 70/80 dB and max SPL, but treble is not a separate scored metric; no explicit >16 kHz figures surfaced — [RTINGS TV frequency response](https://www.rtings.com/tv/tests/sound-quality/frequency-response)
- Bluetooth: SBC reportedly cuts content above ~14 kHz (forum-grade claim); aptX reportedly retains 10 Hz-22 kHz but adds quantisation noise at HF; AAC HF cutoff "varies widely from device to device" — [Habr codec article](https://habr.com/en/articles/456476), [Hydrogenaudio](https://hydrogenaudio.org/index.php/topic,117410.0.html), [SoundGuys codec guide](https://www.soundguys.com/bluetooth-audio-codecs-ultimate-guide-20296/)
- Phone-to-phone Google Nearby ultrasonic protocol (18.5-20 kHz) shows commodity phone speakers can be used, i.e. consumer hardware can radiate this band — [Getreuer et al., IEEE TMM 2018 (academia.edu copy)](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)

### Inferences
- A 64 dB(Z) level at 20 cm is far below what a 80-90 dB@1m-class pro emitter gives; laptop speakers are the weakest link, so 120-seat rooms relying on laptop/projector speakers are the highest risk.
- Bluetooth SBC (and possibly AAC) output paths should be treated as failing for 17-20 kHz; use wired (HDMI/analog/USB) with 48 kHz sample rate (Nyquist 24 kHz; 44.1 kHz leaves only ~2 kHz margin above 20 kHz) — design inference, not sourced.
- Tweeter roll-off: small drivers/class-D laptop amps have no guaranteed output at 19 kHz; marketing "20 kHz" spec sheets (e.g. RS PRO/Logitech PC speakers) are unqualified (no ±dB) and should not be trusted.

### Gaps
- No measured >16 kHz responses for specific MacBook/Windows laptop models, projector speakers (e.g. Epson/Optoma 10 W), TVs, or ceiling/PA speakers (including 100 V line transformers, which often roll off above 15-18 kHz); no -3 dB points found.
- No sourced behaviour of class-D amp output filters/DSP at 19 kHz; no HDMI audio-sample-rate behaviour for projectors/TVs (many downsample or process internally).

## 2. Audio-chain processing that notches or kills a steady high tone

### Takeaway
Feedback suppressors use very narrow notches (~1/60-1/80 octave) and would treat a sustained pure tone as feedback, but only on mic channels; a playback-only chain is safer. No sourced evidence found on TV enhancement modes, OS limiters, or loudness equalisation at 19 kHz.

### Cited Findings
- dbx AFS2 analyses 24 bands/channel, lowers a feedback frequency within milliseconds; notches as narrow as 1/80 octave; "Live" mode (for music) allows more gain before suppression — [Sound On Sound review](https://www.soundonsound.com/reviews/dbx-afs2), [dbx AFS2 brochure](https://www.fullcompass.com/common/files/21776-dbxAFS2Brochure.pdf)
- Behringer FBQ2496 locates up to 20 feedback frequencies/channel and applies extremely narrow notches — [Thomann FBQ2496](https://www.thomannmusic.com/behringer_fbq_2496_feedbackdes.htm)
- Shure SCM410 automatic mic mixer (IntelliMix) uses gating of mics to reduce feedback/comb filtering (gating, not notching); relevant only to mic channels — [Full Compass SCM410](https://www.fullcompass.com/prod/020621-shure-scm410-4-channel-half-rack-automatic-mic-mixer)

### Inferences
- If the beacon is injected into a PA mixer input that passes through an AFS-type processor, a steady tone near-equal to a feedback-like signature may be notched after some seconds; mitigate by sending the beacon as bursts/frequency-hopped chirps/spread-spectrum (like Google's DSSS) rather than a continuous tone, and by bypassing the suppressor (inject post-DSP or on a separate zone/amp).
- Limiters/compressors triggered by loud speech will duck the tone if it is mixed with the programme; sum it after the limiter or reserve headroom.
- Windows "Enhancements"/Loudness Equalization, macOS output EQ, TV "clear voice/dialogue/night mode" typically band-limit or compress; none sourced.

### Gaps
- No primary documentation on TV audio-enhancement modes, Windows/macOS enhancement or limiter behaviour at 17-20 kHz, speaker "night mode", or sample-rate conversion of HDMI sinks.
- No source on whether specific suppressors actually notch a 19 kHz tone (many suppressors' detection range stops below 20 kHz).

## 3. Physics: air absorption, spreading, directivity, reflections, absorption, link budget

### Takeaway
At 19 kHz, 20 C, 50% RH air absorbs ~0.48 dB/m, so ~14 dB extra at 30 m plus 29.5 dB spherical spreading: ~44 dB total below the 1 m level at 30 m on-axis in free field (~47 dB at 30% RH). Absorption is humidity-dependent in a non-monotonic way and worse in dry winter indoor air.

### Cited Findings
- ISO 9613-1:1993 tabulates 50 Hz-10 kHz only; formulae extend to ultrasonic frequencies — [Sengpiel Audio formula page](https://sengpielaudio.com/AirdampingFormula.htm), [ISO 9613-1 listing](https://online.standard.no/en/iso-9613-1-1993)
- Calculated by researcher using the ISO 9613-1 formula (101.325 kPa), alpha dB/m. Columns 16 / 18 / 19 / 20 kHz:
  - 20 C, 20% RH: 0.435 / 0.472 / 0.489 / 0.506
  - 20 C, 30% RH: 0.466 / 0.534 / 0.566 / 0.598
  - 20 C, 40% RH: 0.420 / 0.499 / 0.539 / 0.578
  - 20 C, 50% RH: 0.365 / 0.443 / 0.483 / 0.524
  - 20 C, 60% RH: 0.318 / 0.391 / 0.429 / 0.468
  - 20 C, 80% RH: 0.252 / 0.314 / 0.346 / 0.380
  - 10 C: 30% RH 0.335 (18k)/0.355 (20k); 50% RH 0.447/0.494. 25 C: 30% RH 0.536/0.622; 50% RH 0.385/0.464
  - (Method source: formula at sengpielaudio; output unverified against ISO tables outside 10 kHz — verify.)
- Real-room evidence: Google reports reliable at 2 m, often at 10 m for 18.5-20 kHz phone-to-phone — [Getreuer et al.](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware); laptop link reached 5 m with QPSK at 100% volume — [arXiv 2103.11261](https://ar5iv.labs.arxiv.org/html/2103.11261)
- Device-orientation/off-axis loss: lower received power at device sides than front in the laptop study — [arXiv 2103.11261](https://ar5iv.labs.arxiv.org/html/2103.11261)

### Inferences (researcher arithmetic)
- Spreading 20log10(r): 5 m = 14.0 dB, 15 m = 23.5 dB, 30 m = 29.5 dB (re 1 m).
- Air at 19 kHz, 20 C/50%: 5 m = 2.4, 15 m = 7.2, 30 m = 14.5 dB. At 30% RH: 2.8 / 8.5 / 17.0 dB.
- Total free-field loss vs 1 m: ~16 dB @5 m, ~31 dB @15 m, ~44 dB @30 m (~47 dB dry).
- Link budget example: laptop at ~64 dB SPL @0.2 m → ~50 dB@1 m (the 64 dB is Z-weighted full level, rough) → ~6 dB at 30 m free-field: below the 48.8 dB(Z) broadband lab floor (though in-band 18-20 kHz noise is much lower than broadband Z). A 90 dB@1 m PA/HF-capable emitter → ~46 dB at 30 m. Treat laptops as unable to cover 30 m; need a dedicated emitter ≥85-95 dB@1 m at 19 kHz or multiple distributed emitters (halves max distance to ~10-15 m each).
- Beaming: tweeters beam strongly at 19 kHz (wavelength 1.8 cm); off-axis students at sides/back may lose 10-20+ dB — typical physics, unsourced figure; verify by measurement. Seats/audience absorb HF strongly and shadow rear rows (floor-grazing path); raked halls help. Standing waves are weak at this wavelength but multipath fading (cm-scale notches) is severe for a pure tone; wideband/spread-spectrum helps.

### Gaps
- No sourced data for absorption by seats/people/curtains/tiles at 19 kHz, speaker directivity numbers, or measured RT60/standing-wave data for 120/240-seat halls; no vendor range claims verified (Chirp/Lisnr/Sonarax not retrieved).

## 4. Interference and noise

### Takeaway
Walls/closed doors effectively contain near-ultrasound; open doors leak. Noise sources were not quantified in sources found.

### Cited Findings
- Closed door: 87% (1110/1280) of messages received from same-space beacon, 0/1440 from other space; open door: 27% (171/640) leaked from other space (Pixel v1, ~21 kHz) — [Using Inaudible Audio for Indoor-Localization, arXiv 2002.00091](https://ar5iv.labs.arxiv.org/html/2002.00091)
- High-frequency sound is "mostly reflected by walls"; devices sharing a token are roughly in the same room — [Getreuer et al.](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- Teleconference and café music did not affect performance (no noise measurements); the laptop system stayed robust alongside ultrasonic occupancy sensors — [arXiv 2002.00091](https://ar5iv.labs.arxiv.org/html/2002.00091), [arXiv 2103.11261](https://ar5iv.labs.arxiv.org/html/2103.11261)
- Door gaps: a gap <3 mm can cancel the benefit of an acoustic door; STC covers only 125-4000 Hz so says nothing at 19 kHz — [Construction Specifier](https://www.constructionspecifier.com/effective-sound-control-of-doors/)

### Inferences
- Adjacent-room leakage via open doors (~27% message leak) means room-binding needs a time-varying/room-specific code, not just presence of tone. Bag/pocket attenuation (46% near door vs 3% phone-receiver, same paper) matters for students' phones in bags.

### Gaps
- No measured numbers for HVAC, projector fan/ballast whine, LED/dimmer, SMPS, fluorescent, key-jingle noise at 17-20 kHz; no wall/door TL at 19 kHz in dB; no source on students' phones' own chirps.

## 5. Practical emitter design and calibration

### Takeaway
Evidence-limited: design guidance is mostly inference from the above, plus the Google DSSS approach.

### Cited Findings
- Google Nearby uses direct-sequence spread spectrum, claimed robust to multipath, motion, narrowband noise; raw 94.5 b/s — [Getreuer et al.](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- Laptop link results show volume matters: 5 m at 65-80% volume gave BER ~0.1, but 100% volume BER <2e-4/0.01 — [arXiv 2103.11261](https://ar5iv.labs.arxiv.org/html/2103.11261)

### Inferences
- Use low-rate spread-spectrum/repetition with long integration (seconds), avoid clipping (leave -6 dBFS headroom; clipping produces audible intermodulation and wastes power), prefer sending from a fixed room PC via wired path at 48 kHz (not Bluetooth), mix after limiters, avoid AFS-processed paths.
- Multiple emitters spaced <=10-15 m in the 240-seat hall; aim tweeters at audience, mount high; use different codes/time-slots per emitter to avoid destructive interference nulls.
- Calibration: play test signal, measure with a reference (phone app + calibrated mic or the target app's own SNR) on a grid (e.g. every 2-3 rows x 3-4 seats, plus corners/back/side seats, with students seated and sample humidity), build dB map, require margin >=10 dB SNR at the worst seat; re-test with HVAC on and door open.

### Gaps
- No vendor range claims, no standard procedure sourced, no dB-map examples found.
