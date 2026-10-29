# Phone-side capture of near-ultrasound (17-20 kHz) in a browser/PWA, as of late 2026

(Research depth note: ~17 tool calls. Two key PDFs (SonicPACT arXiv 2012.04770, arXiv 2103.11261) returned binary; only abstract-level/snippet facts from them are used. No device-by-device mic attenuation table was found.)

## 1. Microphone frequency response above 16 kHz (iPhone, Android tiers) and published measurements

### Takeaway
There is no public per-model table of 18-20 kHz mic attenuation. What exists: Android's CDD sets a pass criterion (mic mean response 18.5-20 kHz within 15 dB of 2 kHz) only for devices that opt in via a property; iPhone 7/8 measurements show roll-off above ~19-20 kHz; research and Google's Nearby settle on 18.5-20 kHz but with big device variance. Budget-phone behaviour is essentially undocumented, so per-device runtime testing is required.

### Cited Findings
- Android CDD (section 7.8.3): "Near-Ultrasound" is defined as 18.5-20 kHz. If a device reports PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND=true, mic mean power response in 18.5-20 kHz must be no more than 15 dB below the response at 2 kHz, and SNR >= 80 dB. Speaker (PROPERTY_SUPPORT_SPEAKER_NEAR_ULTRASOUND) must be no lower than 40 dB below its 2 kHz response. Near-ultrasound support is only "strongly recommended" for devices with mic/speaker, so it is optional. — [AOSP CDD commit](https://android.googlesource.com/platform/docs/source.android.com/+/20cdbb7%5E%21)
- Android UNPROCESSED source CDD (5.11): mid-band 100 Hz-7 kHz within +-10 dB; 7-22 kHz must be within +-30 dB of mid-band level (a very loose bound: 30 dB down is allowed at 19 kHz); SNR >= 60 dB. — [CDD 5.11 mirror](https://os.unplugged.com/werunplugged/unplugged-vendor/raw/branch/main/compatibility/cdd/5_multimedia/5_11_unprocessed-audio.md), [typo-fix commit](https://android.googlesource.com/platform/compatibility/cdd/+/d7493fb%5E%21/)
- SonicPACT (MIT/Google-affiliated) measured iPhone 7 and 8: responses "drop sharply above 20 kHz" but may pass 17-19 kHz; they used 18.5 kHz carrier because iPhone 8 response attenuates significantly above 19 kHz; digital 48 kHz rate could represent up to 24 kHz but analog hardware likely not optimized. — [SonicPACT arXiv](https://arxiv.org/pdf/2012.04770) (via search snippet; also [ar5iv](https://ar5iv.labs.arxiv.org/html/2012.04770))
- Google Nearby (Getreuer et al., IEEE TMM 2018) uses 18.5-20 kHz on commodity phones; raw ~94.5 bit/s, reliable at 2 m, often works at 10 m. — [Getreuer summary](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- ACM TIoT 2026 re-evaluation (Putz et al., arXiv 2602.02249; ~11,900 real recordings, 31 papers reviewed): each device has a different frequency response especially near-ultrasonic; commonly 44.1 kHz limits bandwidth below 22 kHz; up to 30 dB SPL spread between phones at same volume setting; nonlinear distortion above ~75% volume; Pixel 4a and Pixel 6 Pro "largely failed" as receivers for the DigitalVoices scheme; in the 5-phone experiment (Pixel 4a, Pixel 6 Pro, Nexus 6P, Oppo Reno 6, Galaxy S20 Ultra) Gonçalves et al. TER ranged 5-32%; putting receiver in a pocket degraded all schemes; Nearby performed worse in the device-model room than distance room (multipath). Their recordings used Android MIC source except Pixel 4a also offering UNPROCESSED. — [arXiv HTML](https://arxiv.org/html/2602.02249)
- NCC Group whitepaper: handset mics flat to ~14 kHz but able to hear up to ~24 kHz in many cases; headsets often better at top. — [NCC](https://nccgroup.com/media/00rjhgp1/_ultrasound-whitepaper-v10.pdf) (search snippet only)
- A patent notes many terminals have detectable upper limit around 21 kHz due to mic characteristics/low-pass filtering. — [US 9,709,655 family](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9709655) (snippet)
- BatNet used 20-24 kHz PSK (600 bit/s to 6 m) but authors judged it not worth pursuing vs BLE. — [BatNet](https://deepai.org/publication/batnet-data-transmission-between-smartphones-over-ultrasound)

### Inferences
- Design the tone/chirp band at ~17.5-19 kHz rather than 19-20 kHz; 19+ kHz is where iPhone 7/8-class mics and unknown budget mics fall off, and 20 kHz+ is unsafe given 44.1 kHz Nyquist (22.05 kHz) with anti-alias roll-off.
- Because CDD allows UNPROCESSED to be 30 dB down above 7 kHz, "passes CDD" does not guarantee a usable 19 kHz SNR; a link budget with >=20-30 dB margin and a per-phone self-test is prudent.

### Gaps
- No public per-model measurements (iPhone 12-17, Galaxy A-series, Redmi, etc.) of 18-20 kHz mic attenuation found; no vendor (Chirp, Lisnr, Sonarax, CourseKey, SilverPush) published coverage percentages (search returned nothing).
- The Android near-ultrasound property's real-world share of devices reporting true: not found.
- iPhone MEMS mic datasheets (Knowles/Goertek etc.) not identifiable from public sources.

## 2. Sample rate (44.1 vs 48 kHz, 16/8 kHz, Nyquist)

### Takeaway
Modern iOS and Chrome typically deliver 48 kHz (iOS 26/27 reports show 48000 for mic and contexts), 44.1 kHz appears in older Safari and some Android hardware; 19 kHz is representable at both but anti-alias filter roll-off near 20-21 kHz at 44.1 kHz eats the top. 16/8 kHz only arises via Bluetooth HFP/voice paths, where 19 kHz is impossible.

### Cited Findings
- 2017-era compatibility table: Safari 11 fixed 44100 Hz while Chrome/Firefox 48000 (old). — [AddPipe search hit](https://blog.addpipe.com/getusermedia-audio-constraints.md)
- 2026 WebKit bug: on iOS 27 beta all AudioContexts and the microphone report 48000 Hz and getSettings().sampleRate is 48000. — [WebKit 326286](https://bugs.webkit.org/show_bug.cgi?id=326286)
- WebKit report: after page restore on iPhone, AudioContext sample rate changed to 48000; workaround of creating/closing a throwaway AudioContext. Mismatched context rate vs hardware rate gives glitches (fixed). — [WebKit 219201](https://bugs.webkit.org/show_bug.cgi?id=219201)
- Mozilla: mismatch of rates causes ~0.23% drift and buffer build-up. — [Mozilla wiki](https://wiki.mozilla.org/Special:MobileDiff/669392)
- iOS 26.4 report: opening mic with echoCancellation false still lowered output quality incl. reduced sample rate and stereo to mono. — [WebKit 311451](https://bugs.webkit.org/show_bug.cgi?id=311451) (via search snippet)
- Bluetooth: HFP 1.6 mSBC = 16 kHz mono wideband; otherwise CVSD 8 kHz; codec negotiated by OS/headset, not the browser. — [mailing list](https://lists.tlug.jp/ML/1611/msg00009.html), [Infineon](https://community.infineon.com/t5/Wi-Fi-Bluetooth-for-Linux/How-to-find-if-the-BT-headset-supports-16khz-or-only-8khz-sampling-rate/m-p/387119)
- Android CDD raw capture: listed sample rates must be captured without up-sampling and down-sampling needs anti-alias filtering. — [AOSP CDD](https://android.googlesource.com/platform/docs/source.android.com/+/20cdbb7%5E%21)

### Inferences
- Read track.getSettings().sampleRate and audioContext.sampleRate at runtime; if <44100 declare "cannot hear" immediately (Nyquist 8 kHz or 4 kHz for BT). Request AudioContext({sampleRate: 48000}) only after testing; a mismatched rate invokes resampler that may add its own filtering.
- At 44.1 kHz, a 19 kHz tone sits only 3 kHz from Nyquist; implementations' anti-alias filters commonly roll off in the last few kHz (uncited engineering knowledge) - another reason to keep carriers <=19 kHz.

### Gaps
- No authoritative per-device table of which Android handsets deliver 44.1 vs 48 kHz to Chrome; not found.

## 3. Browser behaviour and OS voice processing

### Takeaway
Safari/iOS historically low-passed mic audio (~9-13 kHz) because getUserMedia used VoiceProcessingIO; setting echoCancellation:false is the critical lever and now (per WebKit 2023) avoids VPIO, but iOS 26/27 still have bugs and side effects (earpiece routing, mono/lower quality). Chrome Android reportedly passed an 18 kHz tone in 2018-19 tests; but no current evidence on Samsung Internet, Firefox Android, or OEM audio-source mapping was found.

### Cited Findings
- WebKit 179411 (Nov 2017): with echoCancellation/noiseSuppression/autoGainControl false, Safari 11 still low-passed; ultrasonic test bands near 3/8/14/18/20 kHz; iOS 12.1 iPhone 7 analyser returned zeros above ~10 kHz; 2019 white noise showed cutoff ~12 kHz; getSupportedConstraints omitted echoCancellation. Fix r252681 (2019-11-19): applyConstraints({echoCancellation:false}) on the track works as a workaround; echoCancellation false also disables AGC; closed 2019-11-20; follow-ups 204444, 204467. — [WebKit 179411](https://bugs.webkit.org/show_bug.cgi?id=179411)
- WebKit 192932 (iOS 12.1): 18 kHz tone detected in Chrome but not Safari; 2023 comment by Youenn Fablet: Safari no longer uses voice processing (VPIO) when echoCancellation is false. Resolved "configuration changed", no confirmation test. — [WebKit 192932](https://bugs.webkit.org/show_bug.cgi?id=192932)
- WebKit 184646: content above ~11 kHz mirrored (aliasing) in Safari 11. — [WebKit 184646](https://bugs.webkit.org/show_bug.cgi?id=184646)
- WebKit 204467: getUserMedia({audio:true}) enables multiple filters by default; reporter says FFT correct in Chrome on macOS/iOS/Android once disabled. — [WebKit 204467](https://bugs.webkit.org/show_bug.cgi?id=204467)
- iOS 27 beta (bug 326286): with echoCancellation:true output crackles; with false playback clean but output routes to earpiece receiver at much lower volume; applyConstraints on a live track doesn't change route, replaceTrack with new track does; native AVAudioSession .playAndRecord without voice processing is clean. Open, P2. — [WebKit 326286](https://bugs.webkit.org/show_bug.cgi?id=326286)
- 2024-2026 reports: echoCancellation:false yields stereo/mono quirks; channelCount ignored. — [Apple forums](https://developer.apple.com/forums/thread/804765)
- Non-Safari iOS browsers/WebViews: in 2018 only Safari exposed getUserMedia on iOS. — [WebKit 179411](https://bugs.webkit.org/show_bug.cgi?id=179411)
- Chrome: with echoCancellation omitted, uses hardware AEC if present else software AEC; Chrome 64 experiment disables hardware noise suppression when AEC enabled (macOS only). — [Chrome blog](https://developer.chrome.com/blog/disabling-hardware-noise-suppression/), [more native AEC](https://developer.chrome.com/blog/more-native-echo-cancellation/)
- W3C thread 2017: Chrome echoCancellation:false plus goog* constraints (googHighpassFilter) was needed for unprocessed; highpass default could alter data. — [W3C log](https://lists.w3.org/Archives/Public/public-media-capture-logs/2017Jun/0118.html)
- Firefox: closed bug where FFT cut off at high frequencies tied to inability to disable processing. — [Mozilla 1141739](https://bugzilla.mozilla.org/show_bug.cgi?id=1141739)
- voiceIsolation constraint: third-party docs claim iOS/macOS Safari support (unverified); Apple forum says mic modes (Standard/Voice Isolation/Wide Spectrum) are user-controlled via Control Center with no public API for apps to force them. — [VDO.Ninja docs](https://docs.vdo.ninja/advanced-settings/audio-parameters/and-voiceisolation), [Apple forum](https://developer.apple.com/forums/thread/844448)
- Research recordings on Android used MIC source (not VOICE_COMMUNICATION), with UNPROCESSED available on Pixel 4a. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)

### Inferences
- Constraints to request: {echoCancellation:false, noiseSuppression:false, autoGainControl:false, channelCount:1} plus voiceIsolation:false where accepted, then verify via track.getSettings() (browsers report what was applied); on Safari also call applyConstraints() after acquisition (historic workaround). Always verify empirically with the self-test, since a "false" setting does not prove no OS-level DSP.
- Android Chrome: I found no source confirming which AudioSource Chrome uses when processing is off (MIC vs UNPROCESSED vs VOICE_RECOGNITION); unknown OEM DSP (e.g., Samsung/Xiaomi voice enhancement) cannot be excluded. A native app could choose UNPROCESSED explicitly (check AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) and AVAudioSession mode .measurement on iOS (the latter is uncited knowledge).
- Avoid playing audio on the same page while mic is open on iOS with echoCancellation false (earpiece routing).

### Gaps
- Current (2025-26) test results for Chrome Android, Samsung Internet, Firefox Android on 18-19 kHz: not found.
- Whether iOS 26/27 Safari still attenuates 17-20 kHz after the 2023 VPIO change: no measurement found.
- WebKit 311451 and Apple forum 804765 details were only seen as snippets.

## 4. Permission and lifecycle failures

### Takeaway
iOS standalone PWAs are the weakest environment: permission re-prompts, streams stop on navigation, mic lost on background/lock. Plan for a foreground, screen-on, single-page "listening" session started by a user gesture, with track-ended recovery.

### Cited Findings
- WebKit 215884/212040: in Home Screen web apps the getUserMedia prompt reappeared on route/hash navigation (streams stopped on main route change); grant not preserved across app restarts; reported persisting on iOS 16.3.1; conflicting reports of fixes. Mitigation: acquire stream once and avoid navigation. — [WebKit 215884](https://bugs.webkit.org/show_bug.cgi?id=215884), [212040](https://bugs.webkit.org/show_bug.cgi?id=212040)
- iOS 13.2.3: after ~30 s on home screen the audio track readyState became "ended"; re-calling getUserMedia on visibilitychange restores it. — [WebKit 204681 area/search results](https://bugs.webkit.org/show_bug.cgi?id=204681)
- iOS 15.5 Safari: mic stopped on auto-lock while speaker audio continued; maintainer couldn't reproduce on 15.6 beta 3. — [WebKit 241400](https://bugs.webkit.org/show_bug.cgi?id=241400)
- Comment (unverified user claim): installed web apps lose the mic when backgrounded/locked while Safari tab sometimes keeps it. — [WebKit 239602](https://bugs.webkit.org/show_bug.cgi?id=239602)
- WKWebView: mic muted in background; native AVAudioSession capture continues. — [Apple forum](https://developer.apple.com/forums/thread/689182)
- Bluetooth: mic use forces headset onto HFP (8 kHz CVSD or 16 kHz mSBC) with much lower quality than A2DP; browser cannot choose. — [Fairphone forum](https://forum.fairphone.com/t/low-quality-audio-with-bluetooth-headset-during-phonecalls-whatsapp-calls/64755), [Infineon](https://community.infineon.com/t5/Wi-Fi-Bluetooth-for-Linux/How-to-find-if-the-BT-headset-supports-16khz-or-only-8khz-sampling-rate/m-p/387119)
- Pocketed receiver degraded all schemes in the 2026 study. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)

### Inferences
- Enumerate devices (enumerateDevices/devicechange) and track.label; if label suggests Bluetooth/AirPods or sampleRate <44100, prompt user to disconnect or select the built-in mic (on iOS the OS may override).
- Use Screen Wake Lock API to prevent lock during the capture window (uncited; support on iOS PWAs should be verified). Listen for track 'ended'/'mute', visibilitychange, and AudioContext 'statechange'; call audioContext.resume() inside a user gesture.
- Camera+mic simultaneous use, phone case/hand occlusion: no sources found; detect via self-test/ambient-level monitoring (below). Hand/case cover can attenuate high frequencies disproportionately (physics inference, not cited).

### Gaps
- AudioContext suspended-until-gesture, simultaneous camera+mic effects on processing, phone case/hand attenuation numbers: no citable sources found.
- Whether Safari 26/27 standalone PWAs still re-prompt each launch: unverified for 2026.

## 5. Runtime detection and calibration

### Takeaway
Everything above can be checked at runtime except true OS-DSP internals; a short self-test (play or receive a known pilot tone/chirp and measure in-band vs reference-band energy) gives a per-phone verdict. Published research supports calibrating per device because responses vary up to tens of dB.

### Cited Findings
- Per-device frequency response and speaker output vary widely (30 dB SPL spread; different near-ultrasound responses), motivating device-specific testing. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)
- Reporters detected Safari's filtering by Goertzel/FFT analysis of an 18 kHz test tone vs Chrome. — [WebKit 192932](https://bugs.webkit.org/show_bug.cgi?id=192932)
- Google Nearby and the academic recommendations treat the acoustic channel as a low-bandwidth trigger, supplemented by another link. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)

### Inferences (proposed device capability profile)
1. Read getSettings(): sampleRate, echoCancellation, noiseSuppression, autoGainControl, channelCount, deviceId/label (flag Bluetooth). Fail if rate <44.1 kHz.
2. Noise-floor probe (2 s, no signal): compare band energy 17-20 kHz vs 1-4 kHz and vs 10-15 kHz via AnalyserNode/AudioWorklet. White-noise/ambient spectra that show a hard cliff (e.g., at 9-13 kHz as in old Safari) or mirrored energy indicate low-pass or aliasing.
3. Active test: teacher/reference device emits pilot tones at 15, 17, 18.5, 19.5 kHz (or a swept chirp); the student phone reports SNR per tone; accept the highest tone with >=10-15 dB SNR. Also useful for self-loopback check (play from own speaker, record own mic) but speaker response is a confound, and on iOS playing while recording may reroute audio.
4. Continuously monitor: track.onended/onmute, AudioContext state, input level, pilot-tone SNR drift (detect hand/case/pocket) and surface "uncover microphone" warnings.
5. Fallbacks: lower carrier (17-18 kHz), longer integration/spread-spectrum code, or non-acoustic backup (BLE not available in iOS Safari; QR/code entry - uncited design suggestion).
6. Native app improvements: choose UNPROCESSED/measurement audio source, keep capture in background, control sample rate, no PWA re-prompt, access BLE; check Android PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND.

### Gaps
- No published study quantifying what fraction of phones fail a 19 kHz self-test; no evidence of vendor SDKs' device coverage numbers.
