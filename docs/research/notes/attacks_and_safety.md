# Attacks on, and safety/legal issues of, a 17-20 kHz classroom-presence signal

Scope note: ~19 tool calls; web search is a summarising tool, several figures below come via summaries of the cited page and were not verified against the primary text (flagged "unverified"). Items without a source are in Inferences or Gaps only.

## Q1. Record-and-replay / relay: can the signal travel through WhatsApp / Meet / Zoom / FaceTime / Teams?

### Takeaway
Relay over voice-call apps is plausible only for apps/modes that code fullband (48 kHz) audio and do not denoise. Opus fullband tops out at 20 kHz, so 17-19 kHz can in principle survive; typical narrow/wideband voice calls (8/16 kHz sample rate) cannot carry it. Concrete per-app facts are thin; vendors do not document this. A direct-file relay (WhatsApp/Telegram media sent as audio file, screen recordings) is a different matter and is likely the simplest relay. Plan on testing empirically rather than relying on codec limits as the defence.

### Cited Findings
- Opus (RFC 6716) audio bandwidth/effective sample rate: NB 4 kHz/8 kHz; MB 6/12; WB 8/16; SWB 12/24; FB 20 kHz/48 kHz. "Opus never codes audio above 20 kHz." So only fullband mode can carry 17-20 kHz; SWB (12 kHz bandwidth) and below cannot. — [RFC 6716](https://datatracker.ietf.org/doc/html/rfc6716)
- WhatsApp: only evidence found is one 2019 WhatsApp voice-NOTE (not call) file: Opus, mono, 16 kHz sampling, ~19.1 kbps. A 16 kHz rate means max 8 kHz bandwidth, so 19 kHz is impossible in voice notes. Live-call settings not confirmed. — [ffmpeg-user thread](https://ffmpeg.org/pipermail/ffmpeg-user/2019-July/044852.html)
- Zoom: "Original Sound"/High Fidelity music mode (since Zoom 5.2.2, Sept 2020) raises the codec to 48 kHz, 96 kbps mono / 192 kbps stereo and disables Zoom's audio processing (echo cancellation/enhancement). Default Zoom meeting audio is reported by users at 32 kHz (community post, unofficial), i.e. up to ~16 kHz bandwidth, which would drop 17-19 kHz. Zoom support says 16-48 kHz sample rates are acceptable. Zoom Video SDK exposes `hifi` / `stereo` original-sound options. — [Scoring Notes](https://www.scoringnotes.com/news/better-music-experiences-come-to-zoom-with-high-fidelity-audio/); [Podcast Engineering School](https://podcastengineeringschool.com/new-in-zoom-high-fidelity-audio-mode); [Zoom Community](https://community.zoom.com/meetings-2/zoom-audio-rate-48k-27890); [Zoom Video SDK docs](https://developers.zoom.us/docs/video-sdk/web/audio-sound-options.md); [UMich KB](https://teamdynamix.umich.edu/TDClient/30/Portal/KB/Article/1316/Zoom-Enabling-Original-Sound-Music-and-Professional-Audio-Settings)
- Google Meet: ML cloud "denoiser" launched 2020, on by default, trained to separate speech from background noise; processed in Google data centres. No source found on Meet's sample rate/codec; an ML speech-vs-noise model would be expected to suppress a steady non-speech tone (inference, not documented). — [Inquirer/Tom's Guide-era coverage](https://technology.inquirer.net/100833/google-meet-introduces-feature-to-silence-background-noise-during-calls); [TechXplore 2020](https://techxplore.com/news/2020-06-noise-cancellation-google.html)
- FaceTime: Apple FaceTime is named by Fraunhofer as an AAC-ELD user; AAC-ELD family supports 20 kHz bandwidth, max 48 kHz sampling ("Full-HD Voice"). FaceTime's actual rate/mode not confirmed. — [Fraunhofer AAC-ELD](https://iis.fraunhofer.de/en/ff/amm/communication/aaceld.html); [Fraunhofer product sheet](https://www.iis.fraunhofer.DE/content/dam/iis/de/doc/ame/FraunhoferIIS_Product-Sheet_AAC-ELD-Family.pdf)
- General: 16 kHz "wideband/HD voice" is the common WebRTC-style default per third-party summaries (low authority). — [Calilio](https://calilio.com/blogs/voip-codecs)

### Inferences
- Most consumer voice calls (WhatsApp, Teams, Meet at default) are wideband/SWB with noise suppression -> 19 kHz not relayed. Realistic exceptions: Zoom Original Sound/HiFi (48 kHz, processing off), FaceTime (possibly fullband, unverified), screen/audio recordings, or sending a raw .wav/.m4a recorded by the attacker's phone (phone mic sampling at 44.1/48 kHz captures 19 kHz fine). File-relay is the cheaper, more likely attack than live call relay.
- Relay latency (network + codec, ~100+ ms) means any scheme with a tight time-of-flight / tight window nonce check defeats live relay even when bandwidth permits; a pre-recorded replay is defeated by a rolling code.
- A mic input route on a phone usually passes through OS voice processing (AGC, noise suppression) in call mode; the attendance app should itself capture in a raw/"unprocessed" mode, which is also what an attacker's emulator can mimic.

### Gaps
- No vendor-documented statement on WhatsApp live call codec bandwidth, Meet/Teams sample rate, or whether Krisp/RNNoise/Meet denoiser attenuates >16 kHz tones; needs empirical test (play 19 kHz tone through each app between two phones and FFT the far end).
- Screen-recording/recording-sharing: no specific source; trivial in principle since any phone voice recorder captures 48 kHz.

## Q2. Cheap attacks and how rolling codes / challenge-response / nonce / ToF / cross-device checks mitigate

### Takeaway
A static tone or static code is trivially spoofable (any second phone, signal-generator app, pre-recorded file, or virtual audio cable). Literature agrees acoustic channels cannot stop co-located attackers or record-and-playback by themselves; defence needs time-bound, unpredictable content plus a server-side check. Co-location at the classroom scale (a friend in the room) is not preventable by acoustics alone.

### Cited Findings
- Sound-Proof (ambient-sound 2FA) authors admit it cannot withstand co-located attackers; later work (Shrestha, Mahdad, Saxena, ACM TOPS 2024) shows it vulnerable to passive "environment guessing" and active "environment manipulating" remote attackers and proximity attackers; they propose random browser-generated speech (Listening-Watch). — [Sound-Proof arXiv](https://arxiv.org/pdf/1503.03790); [Texas A&M page](https://spies.engr.tamu.edu/dangers-of-zero-effort-second-factor-login-based-on-ambient-audio/); [PDF](https://nsaxena.engr.tamu.edu/wp-content/uploads/sites/238/2025/08/sound-based-2FA.pdf)
- Mayrhofer & Gellersen, "On the Security of Ultrasound as Out-of-band Channel" (IPDPS 2007): analyses attacks on ultrasonic channels; ultrasound propagation limits implicitly support security and relative-position verification; proposes authenticated short messages over ultrasound. — [Lancaster eprint](https://eprints.lancs.ac.uk/id/eprint/13029); [PDF](https://www.cecs.uci.edu/~papers/ipdps07/pdfs/SSN-1569015921-paper-1.pdf)
- Sound-Proximity (Choi, Seo, Lee 2018): defines "record-and-playback attack" against sound-based proximity checks (car keyless entry relay) and claims a method robust to it. — [Korea Univ. record](https://scholar.korea.ac.kr/handle/2021.sw.korea/132149?mode=full)
- Ultrasonic ranging patent ties transmitted bits to the current transmit time and a shared symmetric key to prevent replay (illustrates standard design: time-bound keyed code). — [US 12072407](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12072407)
- UCL/UCSB/PoliMI "On the Privacy and Security of the Ultrasound Ecosystem" (PoPETs 2017): ultrasound beacon deployments are vulnerable; demonstrated beacon injection/spoofing, Tor deanonymisation, data leakage; proposed an Android permission/browser extension to filter ultrasound. — [PoPETs](https://www.petsymposium.org/popets/2017/popets-2017-0018.php)
- Braunschweig study: 234 Android apps contained code listening for ultrasonic beacons; 4 of 35 German retail stores had beacons. — [Hacker News](https://thehackernews.com/2017/05/ultrasonic-tracking-signals-apps.html); [CBS](https://cbsnews.com/news/google-removes-apps-that-use-ultrasonic-frequencies-to-track-users)
- SilverPush: audio beacons used for cross-device tracking; FTC warning letters to 12 developers, Oct 2015; SilverPush said it exited beacon business. NIST Mobile Threat Catalogue lists ultrasonic tracking as threat. — [Wikipedia](https://en.wikipedia.org/wiki/SilverPush); [NIST MTC](https://pages.nist.gov/mobile-threat-catalogue/privacy-threats/PRI-0.html)
- DolphinAttack (Zhang et al. 2017): inaudible >20 kHz AM-modulated commands demodulated by microphone nonlinearity; worked on Siri, Google Now, Alexa etc. Relevance: shows phone mics respond to ultrasound and that ultrasound injection into mics is practical; also that a hostile emitter with a >20 kHz carrier could produce in-band artefacts at the receiver (inference). — [arXiv 1708.09537](https://arxiv.org/pdf/1708.09537)

### Inferences (mitigation mapping; design reasoning, not sourced)
- Static tone/ID: defeated by second phone, tone-generator app, MP3. Mitigation: per-window (e.g. 5-30 s) code derived from HMAC(secret, session-id, time-slot) modulated (FSK/chirp) on the carrier; server validates slot and nonce, accepts only current/previous slot. Pre-generated files fail unless the attacker has the secret; the teacher device generating it is the only source.
- Live relay (friend in room records and streams, or teacher-side leak): window must be short and the code must be only accepted within a small time skew after emission; relay adds hundreds of ms but the window is seconds, so this alone does NOT stop relay -> add the second factor below.
- Challenge-response: student phone also emits an ID/nonce chirp that the teacher device or other nearby phones hear (or server issues nonce, phone emits it); gives reverse-direction evidence of colocation and lets multiple phones cross-check. Cross-device consistency: a student's received signal strength/arrival time should be consistent with others in same room; an outlier (very different SNR profile, clean signal with no room reverb/noise) is flaggable. Time-of-flight: speed of sound ~343 m/s -> ~3 ms/m; phone clock/audio-latency jitter (tens of ms) is too coarse for per-seat ToF, usable only for gross room-level (tens of metres) discrimination.
- Emulator/virtual audio cable/mic hijacking: acoustic checks cannot detect; require device attestation (Play Integrity / App Attest), reject rooted/emulated devices, and prefer a native app over a browser (browser can't attest). Residual risk accepted for casual-cheating threat model.
- Proxy attendance by a friend carrying two phones in the room: not stoppable acoustically; mitigate with random spot-checks, per-session device binding, anomaly analytics (same phone IDs across students, impossible simultaneity).

### Gaps
- No peer-reviewed attack on a specific classroom-attendance ultrasonic system located; no data on actual smartphone mic/speaker response at 19 kHz across Indian budget Android phones (many MEMS speakers roll off above ~16-18 kHz - unsourced, needs test).
- Google Nearby / Tone (Nearby Messages used audible+inaudible audio): not researched; Nearby Messages API has been deprecated (unverified, not searched).

## Q3. Denial / jamming (false absence)

### Takeaway
Narrowband 18-20 kHz jamming is easy and cheap for anyone with a phone/speaker and tone app, and would hit all phones in range; mitigations are detection and graceful fallback, not prevention.

### Cited Findings
- Source evidence specific to jamming attendance systems: none found. Related: public airborne ultrasound (pest deterrents) measured up to ~100 dB SPL near 20 kHz in the field, showing high-level ultrasonic sources exist in everyday environments and can swamp bands. — [Southampton/Leighton eprint](https://eprints.soton.ac.uk/421979)

### Inferences
- Attack types: tone/noise jammer at 17-20 kHz; ordinary loud broadband noise (clapping, keys, air-conditioning hiss) raises noise floor; covering the mic or case/pocket attenuates 19 kHz strongly (high frequencies are highly directional and absorbed); phone orientation and speaker directionality cause natural false absences.
- Mitigations: spread-spectrum/multi-tone with FEC and several carriers (e.g. 17.5, 18.5, 19.5) plus a fallback (BLE beacon, QR/short PIN on projector, teacher manual override); measure in-band noise floor at each phone and report it - a high floor flags "jammed" rather than "absent"; if a majority of phones fail simultaneously, mark the session degraded and not penalise students; log jam events; physically, jamming is detectable and attributable (disciplinary).

### Gaps
- No empirical data on classroom 18-20 kHz noise floors (HVAC, projectors, LED drivers, switching supplies produce ultrasonic noise); needs field measurement.

## Q4. Audibility of 17-20 kHz in young adults; discomfort; exposure limits

### Takeaway
A substantial fraction of 17-25 year olds can hear 17-19 kHz at moderate SPL, and a minority is sensitive enough to find it annoying; reported symptoms (headache, nausea, tinnitus) are anecdotal/disputed but the guideline basis is weak, and the public-exposure ICNIRP-derived limit for the 20 kHz third-octave band is about 70 dB SPL. Keep emission well below that and, ideally, at the lowest SPL that works at the farthest seat.

### Cited Findings
- Brazilian high-frequency audiometry of 51 normal-hearing adults aged 18-29 (9-18 kHz tested, Sennheiser HD 520 II earphones): mean thresholds 16 kHz about 3-5 dB, 18 kHz about 17 dB (dB scale as reported "NA"; not directly SPL). Means at 18 kHz are measurable, i.e. most young adults have usable hearing there under headphone presentation. Did not test 20 kHz. — [PMC9450601](https://pmc.ncbi.nlm.nih.gov/articles/PMC9450601/)
- One study of adults 21-70: under-30s were totally responsive up to 16 kHz and 52.2% responded at 20 kHz; no response at 20 kHz in 51-60 and at 18 kHz in 61-70 (single study; via search summary, primary not opened, unverified). Dadson & King: 18% of 18-25 year-olds did not respond at 15 kHz (very old, different method). — [ncbi search summary](https://pmc.ncbi.nlm.nih.gov/articles/PMC8394048/) (listed in results; claim not fetched)
- Test hardware strongly influences high-frequency threshold estimates; headphones/speaker quality changes results by thousands of Hz. — [Audioholics thread, low authority](https://forums.audioholics.com/forums/threads/frequency-range-hearing-loss.85733/post-970976)
- Guidelines: many bodies set the "ultrasound" lower bound at 20 kHz, but by expressing limits in third-octave bands the 20 kHz band (17.8-22.4 kHz) is covered; the ICNIRP-based public tonal maximum permissible level there is 70 dB SPL, with no amplitude/duration trade-off; the 75 dB values relate to bands centred 8-16 kHz (up to 17.8 kHz). Variation across bodies in the 20 kHz band is about 50 dB; some limits are for workers aware of exposure only. (Via search summary of Leighton papers; check ICNIRP/Leighton primary for exact numbers.) — [Southampton eprint 421979](https://eprints.soton.ac.uk/421979); [Leighton 2018 JASA editorial](https://eprints.soton.ac.uk/423687/1/2018_Leighton_JASA_Editorial_Ultrasound_in_air_version_post_review.pdf); [Acoustics Today Fall 2020](https://acousticstoday.org/issues/2020AT/Fall2020/files/basic-html/page18.html); [Lee Co. TG008](https://www.theleeco.com/uploads/2023/06/TG008-Managing-Ultrasonic-Emissions.pdf)
- Leighton: public have been exposed to VHF/ultrasound over guideline thresholds; guidelines meant for workplaces; evidence base for 20 kHz guideline is thin and 40+ years old; symptoms (nausea, headache, dizziness) reported by some people in public spaces, mostly anecdotal. Pest deterrents measured up to ~100 dB SPL at ~20 kHz. No legal requirement found limiting public exposure; SPL meters not required to be sensitive above 20 kHz. — [Southampton news](https://www.southampton.ac.uk/engineering/news/2016/01/20-tim-leighton-ultrasound.page); [Leighton eprint 417682 abstract page](https://eprints.soton.ac.uk/417682)
- Fletcher, Leighton et al. 2018 double-blind: 20 kHz tone for 20 min vs sham, plus nocebo test. Results not retrieved. — [Sonic Field library entry](https://labs.sonicfield.org/library/effects-of-very-high-frequency-sound-and-ultrasound-on-humans-part-ii-a-double-b)

### Inferences
- With a duty-cycled burst (e.g. 100-300 ms every few seconds) rather than continuous tone, exposure and annoyance both fall; avoid steady pure tones (more annoying/tonal) and sharp onsets (use ramped envelopes to avoid audible clicks from spectral splatter).
- Audible frequency spillage (clicks at tone start/stop, intermodulation in cheap speakers into the audible band) is more likely the actual complaint source than the 19 kHz itself.
- 19-20 kHz is audible to fewer people than 17 kHz; shifting up (19-20 kHz) reduces audibility, but phone speaker/mic response falls too - trade-off to measure.
- Suggested design target (engineering judgement): stay under ~60-65 dB SPL at 1 m on the loudest point and ensure at least 5 dB below 70 dB at any seat; measure with a calibrated mic sensitive to 20 kHz.

### Gaps
- No clean primary percentages for "% of 18-25 year-olds who hear 17/18/19 kHz at X dB SPL in free field"; ISO 7029 tables (statistical distribution of hearing thresholds vs age) typically only go to 12.5 kHz, so they do not directly cover 17-20 kHz (unverified; standard not accessed). Needs a pilot audibility test with students or a specialist (audiology) reference such as high-frequency audiometry papers.
- Fletcher 2018 results and ICNIRP primary limit values not verified.

## Q5. Animals, assistive devices, sensory sensitivities

### Takeaway
Dogs, cats and especially rodents hear well above 20 kHz, so 17-20 kHz is audible to them; no good evidence of harm at low SPL, but long-term effects are unstudied. Hearing aids/CIs process roughly up to 8 kHz so are unlikely to respond directly. Students with hyperacusis/autistic sound sensitivity may be a real subgroup; no data specific to 17-20 kHz.

### Cited Findings
- Hearing ranges (sources disagree): cats up to ~32 kHz (Britannica) or 60+ kHz; dogs ~27-46 kHz; pet rodents up to ~90 kHz; devices "generally safe" for dogs/cats per pest-control sources (not independent); rodents may experience distress; no clear long-term research. — [Britannica](https://www.britannica.com/print/article/613488); [Terminix](https://www.terminix.com/rodent-control/diy/ultrasonic-pest-repellers/); [Catster](https://catster.com/guides/will-ultrasonic-pest-repeller-bother-cat); [Wikipedia: Electronic pest control](https://en.wikipedia.org/wiki/Electronic_pest_control)
- Hearing aid bandwidth: JAAA 2015 found maximum audible frequency through BTE aids from ~3.5 kHz to beyond 8 kHz depending on device. Example patent configurations: hearing aid 250-5500 Hz, CI 1750-8000 Hz (illustrative). CIs split ~200-8000 Hz (low-authority forum). — [JAAA 2015](https://www.thieme-connect.com/products/ejournals/pdf/10.3766/jaaa.26.2.3.pdf)
- Hyperacusis is highly prevalent in autism (review estimates 50-70% with clinically significant decreased sound tolerance); mechanism neurodevelopmental. No source ties 17-20 kHz specifically. — [Audiology Research 2021](https://doaj.org/article/e725419385db418fb5dae622b085a08b); [Hearing Health Matters](https://hearinghealthmatters.org/hearing-news-watch/2023/autism-hyperacusis-sound-sensitivity)

### Inferences
- Classroom animals are rare; main animal risk is campus strays, lab animals or rodent facilities near classrooms; exclude those rooms. Hearing aids/CIs: no found evidence of interference; aliasing from an ADC with insufficient anti-alias filtering is possible but unevidenced - include in the pilot.
- Provide an opt-out (alternate attendance method) for students reporting discomfort, tinnitus, hyperacusis; do not require disclosure of a medical reason.

### Gaps
- Nothing found on ultrasound interference with hearing aids/CIs, tinnitus aggravation at 17-20 kHz, or autism-specific responses at these frequencies.

## Q6. Consent, disclosure, India law (DPDP), and defensibly "not audio recording"

### Takeaway
Designing the app so the microphone is used only for on-device band-limited spectral detection (no audio stored or transmitted) is a strong privacy-by-design position, but legally the output (attendance with identity, time, presence) is still personal data under DPDP, and Android/iOS still treat this as microphone access. Notice and consent (or a valid legitimate-use basis) are still needed; for adult university students the children's-data exemption does not help.

### Cited Findings
- DPDP Rules 2025 notified 14 Nov 2025 (some sources say 13th); phased compliance over 18 months. Requirements: standalone, clear, plain-language notice stating data, purpose, rights, complaint route; consent free, specific, informed, unconditional, by clear affirmative act; notice available in English or any Eighth Schedule language; purpose-limited retention; 72-hour breach notice; extra duties for Significant Data Fiduciaries. — [PIB document](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf); [EY](https://www.ey.com/en_in/insights/cybersecurity/transforming-data-privacy-digital-personal-data-protection-rules-2025); [SCC Online](https://www.scconline.com/blog/post/2025/12/26/digital-personal-data-protection-rules-2025-key-highlights/); [Mondaq](https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified)
- Educational institutions are exempt from parental-consent/tracking-and-behavioural-monitoring restrictions for children's data where processing is limited to educational purposes or safety of enrolled children (Section 9 / Rule 11 Fourth Schedule); definition of "educational institution" unclear for edtech. Section 17 provides separate exemptions (e.g. research/statistical) by notification. Sources differ on placement. Applies to under-18s; some first-year students may be 17. — [Tsaaro](https://tsaaro.com/blogs/regulatory-carve-outs-examining-exemptions-under-the-dpdp-act-2023-and-dpdp-rules-2025); [S.S. Rana](https://ssrana.in/articles/schools-coaching-centres-platforms-processing-childrens-data-are-you-compliant/); [IIT BHU summary](https://iitbhu.ac.in/contents/institute/cf/cis/doc/dpdp_act_rules_summary.pdf)
- Regulatory precedent: FTC (2015) warning letters to app developers for undisclosed ultrasonic-beacon listening; NIST classes covert ultrasonic listening as a privacy threat; recommends scrutinising mic-permission apps. Braunschweig found apps listened silently. These are the reputational/legal analogues to avoid: do not run when not in a session, and disclose. — [Vice](https://www.vice.com/en/article/silverpush-ftc-stop-eavesdropping-with-audio-beacons/); [NIST MTC](https://pages.nist.gov/mobile-threat-catalogue/privacy-threats/PRI-0.html)

### Inferences (design/documentation; not legal advice)
- Documentation to make "not recording" defensible: (1) capture audio frames into a ring buffer in RAM only, apply a band-pass/Goertzel/FFT restricted to the 17-20 kHz band with a low-pass-free path that never exposes a speech-band signal (high-pass filter before any buffering so speech content is physically absent; speech intelligibility lives <8 kHz); (2) no audio API writes to disk or network; only the decoded code/boolean and SNR leave the device; (3) publish a one-page data-flow diagram, source-code or third-party audit, and permission rationale string ("microphone used only to detect classroom attendance tone; no audio is recorded"); (4) mic active only for a short window after the teacher starts a session and a visible indicator; (5) retention schedule for attendance records; (6) hash-pinned build/open-source detector so claim is verifiable.
- Legal posture for India: treat attendance record as personal data; give a DPDP-compliant notice (purpose limitation: attendance only), obtain consent (or rely on institutional legal basis if available - needs counsel), offer a non-ultrasound alternative so consent is not coerced, name a grievance contact. Also add an occupational/safety statement (SPL, duty cycle). Consider other applicable laws: IT Act 2000 interception provisions are about transmission, not relevant if nothing is transmitted (unverified, counsel to confirm).
- Because the browser/OS microphone permission wording says "record audio", students may perceive recording regardless; communication matters as much as engineering.

### Gaps
- Section 7 (legitimate uses) applicability to universities was not found; whether universities qualify as "State" instrumentalities (Section 7(b)/17) is not resolved; no Indian case law or regulator guidance on ambient/ultrasonic sensing; no authoritative legal opinion obtained. Needs counsel review.
- GDPR/EU analogues (ePrivacy) and Indian occupational noise rules (e.g. Noise Pollution Rules 2000, which are audible-band) not researched.
