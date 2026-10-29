# Real-world deployments and field evidence for ultrasonic / near-ultrasonic proximity verification

Evidence labels used below: [MEASURED] = peer-reviewed or reproducible measurement; [VENDOR] = company claim; [PRESS] = journalism; [ANECDOTE] = forum or user report. No Hacker News/Reddit anecdotes were retrievable in this session (searches returned none), so there are zero user-complaint anecdotes here. Treat that as a gap, not as evidence of no complaints.

## Attendance / classroom products (CourseKey, Echo360/Top Hat-type, university pilots)

### Takeaway
I found no independent, quantified evidence of any deployed ultrasonic classroom-attendance product's success rate. CourseKey's sound feature (2017) is documented only by press release and a patent that reports no accuracy figures. The only quantitative proxies are the general near-ultrasound studies in the later sections.

### Cited Findings
- [VENDOR/PRESS] CourseKey (Oct 2017): the instructor's classroom device emits an inaudible sound and student devices in the room log presence. Signals carry unique "leader" codes that student devices must match. The company claims the sound cannot pass through walls, so attendance is verified at classroom level. The article gives no accuracy, range, frequency, adoption figures or fallback behaviour. — [Campus Technology](https://campustechnology.com/articles/2017/10/02/new-coursekey-feature-takes-attendance-with-sound.aspx)
- [VENDOR] CourseKey marketing claims 90+ campuses and 2M+ student check-ins (all check-in methods, not sound-only) and patents for GPS and sound attendance. — via search summary of [CourseKey promo coverage](https://innovationsoftheworld.com/coursekey/)
- [PATENT] US10551496 "verifying participation in a meeting using sound signals": the leader's device transmits a sound signal carrying a unique leader code and attendees' phones decode it. It gives method only, no field accuracy. — [Google Patents](https://patents.google.com/patent/US10551496)
- [ACADEMIC, unverified] A UNIMAS 2020 final-year project, "Audio QR for class attendance", exists but is restricted access, so no results could be read. — [UNIMAS repository](https://ir.unimas.my/id/eprint/34028)
- Non-audio comparators: a Chung-Ang student BLE-beacon project reported about 10 cm mean and about 20 cm max error in one room, with degradation from reflections. This is a student project, not rigorous. — [GitHub BeepBeep-attendance listing](https://github.com/bgb10/BeepBeep-attendance). The SMATCH beacon + face system notes students can check in then leave. — [SMATCH](https://kirim.kmutt.ac.th/converis/portal/detail/Publication/1392443011?lang=en_GB)
- No evidence found about Echo360, Poll Everywhere or Top Hat using ultrasound. I did not search these specifically beyond the generic query, so absence is not confirmed.

### Inferences
- Presence "leader code in the audio" is the shared architecture of CourseKey and the patent. Nobody published failure rates, which suggests either good-enough in-product fallback (e.g. GPS, manual instructor override) or unpublished problems. This is speculation.

### Gaps
- No CourseKey student/faculty complaints, no university IT pages, no Reddit/HN threads retrieved. No controlled study of false positives or negatives for ultrasonic check-in with real students.
- No evidence on what fallback CourseKey uses when sound fails (the article is silent).

## Other presence/proximity systems (Google Nearby, Tez, Chirp, Lisnr, Shopkick, SilverPush, Sound-Proof, ggwave)

### Takeaway
Google, the largest operator, removed ultrasonic from Nearby Messages on Android in Aug 2021 in favour of Bluetooth; Chirp was sold off and its SDK shut down in 2020; SilverPush's audio beacon was shut after FTC action. The surviving ultrasound uses are as a low-bit-rate trigger (Lisnr/Ticketmaster, Tez/Google Pay "Tez Mode") typically alongside another channel. Google gave no public reason for dropping ultrasound (see Gaps).

### Cited Findings
**Google Nearby Messages**
- [PRIMARY] "Nearby Messages on Android will cease supporting ultrasonic advertising and discovery beginning August 1, 2021"; advertising/discovery moved to Bluetooth. The whole API is deprecated and stops working December 2023; migrate to Nearby Connections. — [Google Nearby Messages Android docs](https://developers.google.com/nearby/messages/android/get-started), [overview](https://developers.google.com/nearby/messages/overview)
- Earlier descriptions: Nearby used Bluetooth, BLE, Wi-Fi and near-ultrasonic audio to exchange a pairing code. — [XDA](https://www.xda-developers.com/new-app-shares-links-using-ultrasound/)
- [MEASURED, Google authors] Getreuer et al., IEEE Trans. Multimedia 2018: protocol in the 18.5-20 kHz band using commodity phone speakers and mics. Raw rate 94.5 b/s, direct-sequence spread spectrum with a 127-chip code (robust to multipath, motion, narrowband noise). Indoor field tests: reliable at 2 m, often at 10 m. — [Academia.edu copy of abstract](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware)
- [MEASURED, independent replication, 2026] Putz et al. (ACM TIOT, 8 systems, ~11,900 recordings) re-implemented Google's "Nearby" scheme (18.5-20 kHz, 84 bps): reliable within 1 m, unreliable at larger distances, device-pair anomalies, and "much worse" in one office than another with the same devices. Original claim was PSR above 94% up to 2 m; the replication saw low error below 1 m but higher error at 1 m and 2 m. — [arXiv 2602.02249 (HTML)](https://arxiv.org/html/2602.02249)

**Google Tez / Google Pay "Cash Mode" (India)**
- [PRESS] Cash Mode uses ultrasonic "Audio QR" (AQR). Phones must be in hearing range, work on any iOS/Android phone with mic and speaker, no NFC needed (most Indian handsets lacked NFC). Payment still needs the UPI PIN. — [MIT Technology Review](https://www.technologyreview.com/2017/09/18/242020/googles-new-mobile-payment-system-sends-money-via-sound/), [Google India blog](https://india.googleblog.com/2017/09/introducing-tez-mobile-payments-and.html), [TechSpot](https://www.techspot.com/news/71022-google-unveils-tez-mobile-payment-app-uses-sound.html)
- [PRESS, single source] After the 2018 rebrand to Google Pay the audio QR feature was reportedly renamed "Tez Mode". — [Swarajya](https://swarajyamag.com/amp/story/insta/google-bets-big-on-indian-payments-rebrands-tez-to-pay-will-offer-instant-loans-with-icici-and-hdfc)
- No success-rate data, no complaint data, no discontinuation notice were found.

**Chirp**
- UCL spin-out; encoded bytes into audible or near-ultrasonic audio. Sonos acquired its IP/assets 13 Feb 2020; SDKs no longer accessible after 1 Mar 2020. Reason not stated. — [audioXpress](https://audioxpress.com/news/data-over-sound-pioneer-chirp-acquired-by-sonos)
- [ANECDOTE] A forum post claims Google Pay once used similar technology and that the Chirp app was pulled from stores after the Sonos deal. — [Kodular forum](https://community.kodular.io/t/ultrasonic-alternatives-in-kodular/58475) (weak source)

**Lisnr / Ticketmaster**
- [PRESS] Ticketmaster "Presence" used Lisnr ultrasonic tones for entry verification; reported in production at 32 North American venues at the time. Lisnr claims its technology is on 100M devices (vendor claim, unbroken-down). — [PhoneArena](https://www.phonearena.com/news/Ticketmaster-will-use-audio-data-from-your-phone-to-admit-you-to-events_id95671), [Payments Dive](https://paymentsdive.com/news/ultrasound-payment-authentication-platform-lisnr-looks-to-raise-capital-pa/597121)
- Not verified: Lisnr's current commercial status, BLE fallback, any success-rate numbers.

**Shopkick, SilverPush, ad beacons**
- [MEASURED] TU Braunschweig (Arp et al., EuroS&P 2017): 234 Android apps (out of ~1.3M per some press) listen for ultrasonic beacons; 4 of 35 stores in two European cities emitted beacons; Shopkick, Lisnr, Signal360, SilverPush covered. Shopkick beacons are 18-20 kHz and only detected while an SDK app is open. — [NordVPN summary](https://nordvpn.com/blog/ultrasonic-beacons-privacy-threatening-sound-of-silence/), [Infosecurity](https://www.infosecurity-magazine.com/news/android-apps-with-ultrasonic/), [paper PDF](https://vamos.mlsec.org/docs/2017a-eurosp.pdf)
- [PRESS] SilverPush: CDT told FTC (2015) its tech was in apps on about 18M phones (SilverPush itself claimed 67 apps); FTC sent 12 warning letters in March 2016 over undisclosed TV monitoring; SilverPush said it was ending the Unique Audio Beacon service. — [Wikipedia](https://en.wikipedia.org/wiki/SilverPush), [Tom's Guide](https://www.tomsguide.com/uk/us/silverpush-ftc-android-warning,news-22436.html)
- Alphonso and Sonarax: nothing found.

**Sound-Proof (ambient-audio 2FA, not ultrasonic)**
- [MEASURED] Karapanos et al., USENIX Security 2015: 2,007 logins by 2 subjects (iPhone 5, Nexus 4). EER 0.002. False rejection: office 0.003, train station 0.003, cafe 0.006, phone-in-pocket 0.001, purse 0.003. No rejections at 4/8/12 m in a TV room; a phone behind a closed door was rejected. Silence was the main source of false rejections (needs a noise prompt). False acceptance 0.025 in an office under a similar-environment attack. User study n=32: SUS 91, 0 login failures observed, 4.7 s login vs 24.4 s for Google 2SV. Spectrum above 4 kHz performed worse. — [ar5iv](https://ar5iv.labs.arxiv.org/html/1503.03790)
- Design point: it avoids ultrasound by correlating 50 Hz-4 kHz ambient audio. Vulnerable to an attacker who injects dominant sound. — [patent summary](https://patents.justia.com/patent/11436311)

**ggwave / other open-source**
- [MEASURED, Putz et al.] ggwave "inaudible" mode (15-19.5 kHz, 268 bps): effective up to 20 m, near-perfect in all environments in their tests. ggwave audible (1.8-6.3 kHz): only up to 1 m in office, unusable with ambient noise. Lee et al. (19.5-22 kHz, 15 bps): 0% TER in chamber, works to 40 m, robust to all noise types except pocketing. HRCSS (18-22 kHz, 500 bps): extremely high error even best case. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)
- Caveat: those tests used 5 phones (Pixel 4a, Pixel 6 Pro, Nexus 6P, Oppo Reno 6, Galaxy S20 Ultra), all mid/high-end; no budget phones.

### Inferences
- Google moving Nearby to Bluetooth after years of ultrasound suggests radio was judged more reliable and less intrusive. The stated reason was not found, so this is inference.
- Surviving schemes use low bit rates (15-270 bps), spread-spectrum / long symbols and only transmit a short ID or token; the Putz paper says commercial systems target 200 bps or less.

### Gaps
- No Google statement explaining why ultrasound was dropped from Nearby, no Tez Mode usage or failure stats, no Lisnr or Chirp reliability figures, no Starling/bank-app audio evidence, no Apple/Android proximity-pairing audio evidence, no Quiet.js evidence, no HN/Reddit user reports retrieved.

## Academic field studies of phone coverage at 18-22 kHz, noise and rooms

### Takeaway
The best independent evidence (Putz et al. 2026) shows heavy device-pair and room dependence: up to 30 dB spread between phones at the same volume, severe multipath, pocketing degrading every scheme. No study found gives a clean "X% of phones work at 19 kHz" figure; the only device-count study found (14 phones) concluded "most" can operate above the audible range.

### Cited Findings
- [MEASURED] 14 commercial smartphones' microphone response measured at 15-22 kHz; conclusion: most could be used for systems above the hearing range. Full text not accessible (403), so counts unknown. — [ResearchGate](https://www.researchgate.net/publication/362094978_Frequency_Response_of_Smartphones_at_the_Upper_Limit_of_the_Audible_Range)
- [MEASURED] Putz et al. device heterogeneity: up to 30 dB SPL spread between phones at the same volume; nonlinear distortion above about 75% volume on most devices; usable inaudible bandwidth below 4 kHz because of 44.1/48 kHz sampling; design target of tolerating at least 30 dB SNR fluctuation; commercial systems typically at most 200 bps; Nearby worse in one office than another. More errors in offices than in meeting or lecture rooms. Pocketing degraded all schemes; side-by-side placement was worse than opposite-facing. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)
- [MEASURED] Typical phone ADC at 44.1 kHz with a low-pass cutoff around 22 kHz, so signals near the edge are attenuated and distorted; AirPods limited to 16 kHz and Apple Watch M4A recording filters above 16 kHz. Tested 8 device types in total. — [Rebooting Ultrasonic Positioning (ar5iv)](https://ar5iv.labs.arxiv.org/html/1812.02349)
- A COVID-tracing study found one phone's speaker response dropped about 10 dB above 16 kHz and more when covered. — [arXiv 2008.00431](https://arxiv.org/pdf/2008.00431)
- iPhones can represent signals up to 24 kHz digitally but analog hardware is probably not tuned for that band. — [SonicPACT](https://arxiv.org/pdf/2012.04770)
- Patent text (not measurement): typical phones roll off around 19-20 kHz; younger listeners can hear 18 kHz, so 19-21 kHz is safer. — [USPTO 12033485](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12033485)
- [MEASURED] Laptop-to-laptop 18-20 kHz: 4 kbps up to 5 m (laptops, not phones). — [arXiv 2103.11261](https://arxiv.org/pdf/2103.11261)

### Inferences
- Edge of band (19.5-22 kHz) is where 44.1 kHz phones are weakest, yet the Lee et al. scheme using 19.5-22 kHz worked across the Putz device pairs. Both facts hold: success depends on very low rate and robust coding, not on a flat channel.

### Gaps
- No percentage of phones that can transmit/receive at 19 kHz+. No test on budget Android phones. No field study of ultrasonic attendance or occupancy detection with real rooms/students. Full texts of the 14-phone study and the Rebooting-UPS device table were not readable.

## The Indian context

### Takeaway
Tez targeted India precisely because most phones lacked NFC and the market is dominated by mid/budget Android, but I found no published audio QR reliability data in India and no Indian-phone microphone study. Market data indicates a large budget base where untested hardware dominates.

### Cited Findings
- Tez Cash Mode was designed to work without NFC on any phone with a mic and speaker. — [MIT Technology Review](https://www.technologyreview.com/2017/09/18/242020/googles-new-mobile-payment-system-sends-money-via-sound/)
- [MEASURED, market trackers] India 2024 (Canalys): shipments 155.9M; Vivo 19%, Xiaomi (incl. Poco) 17%, Samsung 16%, Oppo (incl. OnePlus) 12%, Realme 11%. — [Canalys via Kowatek](https://blog.kowatek.com/?p=93107), [Canalys Q1 2024](https://canalys.com/newsroom/india-smartphone-shipments-Q1-2024)
- [MEASURED] IDC Q1 2024: sub-$100 segment 15% share (down from 20%); $100-200 "mass budget" 48% share (up from 44%). Top-3 brands in $100-200 (Vivo, Xiaomi, Samsung) 53%. — [MySmartPrice/IDC-Counterpoint summary](https://www.mysmartprice.com/gear/mobiles/mobiles-news/vivo-xiaomi-samsung-drive-indias-smartphone-market-growth-in-q1-2024-counterpoint-research)

### Inferences
- About 63% of 2024 Q1 shipments (15% + 48%) were under $200, and the installed base is older and cheaper still. Budget phones are the group where ultrasound mic/speaker response has least testing (all research phones above were mid/high-end), so coverage there is unquantified.

### Gaps
- No Indian field data on Tez audio QR success, no complaints, no data on which Indian budget phones fail at 19 kHz.

## Synthesis: what fraction works, design choices, realistic reliability, fallbacks

### Takeaway
There is no defensible published "X% of phones at 19 kHz" number. Best evidence: a robustly coded low-rate near-ultrasonic token works in controlled tests on modern mid/high-end phones at 1-20+ m with near-zero error, but degrades with pocketing, device pairs, room multipath and edge-of-band roll-off. Plan on audio as one channel with a non-audio fallback.

### Cited Findings
- Successful designs: spread-spectrum / long-symbol coding at very low rates (Google 94.5 b/s DSSS; Lee 15 bps; ggwave 268 bps), a short token only, ~200 bps ceiling for commercial systems. — [Academia.edu](https://www.academia.edu/164596957/Ultrasonic_Communication_Using_Consumer_Hardware), [arXiv 2602.02249](https://arxiv.org/html/2602.02249)
- The Putz authors recommend using the acoustic channel as a low-bandwidth trigger (hash/ID) that starts a higher-throughput link such as Wi-Fi or cellular, plus strong error correction and interleaving. — [arXiv 2602.02249](https://arxiv.org/html/2602.02249)
- Fallbacks seen in deployed products: Tez/Google Pay required the user to confirm via UPI PIN on a server-mediated transaction (the audio only discovers the peer); Sound-Proof is server-mediated and designed to fall back to code entry (as I recall; not verified in the extract); Google moved Nearby to Bluetooth.

### Inferences
- Realistic reliability: in lab or office conditions with recent phones, high-90s% single-attempt detection is plausible (Lee PSR above 98%, Google original PSR above 94% to 2 m), but the Putz replication shows claims shrink under replication, and no data covers budget phones or pocketed phones. Plan on single-attempt success well below that for a mixed Indian fleet, with retries over several seconds and a mandatory non-audio fallback (manual code, QR on screen, BLE/Wi-Fi/GPS, instructor override). These numbers are my judgment, not measured figures.
- Because audio is easy to relay and replay (Shopkick beacons are spoofable per the Braunschweig authors), audio should be a presence hint combined with server-side rotating codes, not sole proof.

### Gaps
- No measured percentages of phones/rooms that work at ~19 kHz; no deployment post-mortems with failure statistics; no Sonarax, Alphonso, Starling, Apple/Android pairing data.
