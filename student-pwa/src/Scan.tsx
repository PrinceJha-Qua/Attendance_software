import { useEffect, useRef, useState } from "react";
import { ApiError, call, json } from "../../shared/api";
import { AudioReport, Listener, startListener } from "./audio";
import { EMBEDDING_MODEL } from "./embedding";
import { FaceEvidence, runFaceStep } from "./faces";
import { makeDecoder } from "./scanner";

export type ActiveSession = { id: string; course_name: string; status: "live" | "paused"; checked_in: boolean };
type Result = { status: "VERIFIED" | "REVIEW" | "REJECTED" };

const MESSAGES: Record<string, string> = {
  TOKEN_EXPIRED: "That code just changed. Hold steady on the screen.",
  TOKEN_INVALID: "That is not a code for this class.",
  IN_PROGRESS: "Still processing…",
};
const STOP: Record<string, string> = {
  NOT_ENROLLED: "Your enrollment is not confirmed yet. Ask your professor.",
  REENROLL_REQUIRED: "Your face photo needs to be retaken. Go back to the home page and follow the prompt.",
  SESSION_NOT_ACTIVE: "This class is not accepting check-ins right now.",
  SESSION_NOT_FOUND: "This class is not on your list.",
};

export default function Scan({ session, onDone }: { session: ActiveSession; onDone: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const faceVideo = useRef<HTMLVideoElement>(null);
  const skipFace = useRef(false);
  const [hint, setHint] = useState("Point your camera at the QR on the screen");
  const [result, setResult] = useState<Result | null>(null);
  const [fatal, setFatal] = useState("");
  const [camError, setCamError] = useState(false);
  const [phase, setPhase] = useState<"face" | "qr">("face");
  const [screen, setScreen] = useState("");
  const [noFace, setNoFace] = useState(false);

  useEffect(() => {
    let stopped = false; let stream: MediaStream | null = null; let cam: MediaStream | null = null;
    let challengeId: string | undefined; const ev: { cur: FaceEvidence | null } = { cur: null };
    const lastTried = new Map<string, number>();
    let listener: Listener | null = null; let audioFail: AudioReport["status"] | null = null; let plan: { mode: string; max_age_ms: number; trial_mode: boolean } | null = null;

    // Ultrasound beacon: listen from the start of the check-in so a burst has been heard by the time the QR is read.
    const audioStart = async () => {
      try { plan = await call(`/sessions/${session.id}/audio-plan`); } catch { return; }
      if (plan!.mode !== "burst") return;
      const l = await startListener();
      if ("error" in l) audioFail = l.error === "denied" ? "denied" : "unsupported"; else if (stopped) l.stop(); else listener = l;
    };
    const audioReport = (): AudioReport | undefined => plan?.mode !== "burst" ? undefined : listener ? listener.report(plan.max_age_ms) : { status: audioFail ?? "unsupported" };
    const audioTrialLog = (rep?: AudioReport) => {
      if (!plan?.trial_mode || !rep) return;
      call("/trial/audio-log", json("POST", { session_id: session.id, data: { rep, info: listener?.info ?? null, stats: listener?.stats() ?? null, ua: navigator.userAgent.slice(0, 120) } })).catch(() => {});
    };
    audioStart();

    // Face first (front camera + colour flashes), QR last, so the rolling QR is still fresh when it is submitted.
    const faceStep = async () => {
      let colours: string[];
      try {
        const c = await call<{ challenge_id: string; colour_seq: string[] }>(`/sessions/${session.id}/challenge`, { method: "POST" });
        challengeId = c.challenge_id; colours = c.colour_seq;
      } catch (e) {
        const code = e instanceof ApiError ? e.code : "";
        if (STOP[code]) { stopped = true; setFatal(STOP[code]); }
        return;  // any other failure: carry on without a face; the check-in goes to professor review
      }
      try {
        cam = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
        const fv = faceVideo.current!; fv.srcObject = cam; await fv.play();
        while (!stopped && !ev.cur && !skipFace.current) {
          ev.cur = await runFaceStep(fv, colours, setScreen, () => stopped || skipFace.current);
          if (!ev.cur) setNoFace(true);
        }
      } catch { /* no front camera, or the model failed to load: continue without a face */ }
      setScreen(""); cam?.getTracks().forEach((t) => t.stop()); cam = null;
    };

    (async () => {
      await faceStep();
      if (stopped) return;
      setPhase("qr");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      } catch { setCamError(true); return; }
      if (stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
      const v = video.current!; v.srcObject = stream; await v.play();
      const dec = await makeDecoder();
      while (!stopped) {
        if (v.videoWidth === 0) { await new Promise((r) => setTimeout(r, 50)); continue; }
        const text = await dec.decode(v).catch(() => null);
        if (text && text.startsWith("gk1:")) {
          const seen = lastTried.get(text) ?? 0;
          if (Date.now() - seen > 2500) {
            lastTried.set(text, Date.now());
            if (text.split(":")[1] !== session.id) { setHint("This QR belongs to a different class."); }
            else {
              navigator.vibrate?.(40);
              try {
                const r = await call<Result>(`/sessions/${session.id}/checkin`, json("POST", {
                  qr: text, challenge_id: challengeId,
                  face: ev.cur ? { vector: ev.cur.vector, model: EMBEDDING_MODEL } : undefined, liveness: ev.cur?.liveness,
                  audio: audioReport(), scanned_at_client_ms: Date.now(), device: { decoder: dec.name, w: v.videoWidth, h: v.videoHeight },
                }));
                audioTrialLog(audioReport()); stopped = true; setResult(r); break;
              } catch (e) {
                const code = e instanceof ApiError ? e.code : "NETWORK";
                if (STOP[code]) { stopped = true; setFatal(STOP[code]); break; }
                setHint(MESSAGES[code] ?? "Connection problem. Keep scanning.");
              }
            }
          }
        }
        await new Promise((r) => setTimeout(r, 0));
      }
      stream?.getTracks().forEach((t) => t.stop());
    })();
    return () => { stopped = true; stream?.getTracks().forEach((t) => t.stop()); cam?.getTracks().forEach((t) => t.stop()); listener?.stop(); };
  }, [session.id]);

  if (result) return <main><h1>{result.status === "REJECTED" ? "Could not check in" : "You're checked in ✓"}</h1>
    {result.status === "REVIEW" && <p>Your professor may confirm in person.</p>}
    <button onClick={onDone}>Done</button></main>;
  if (fatal) return <main><h1>Cannot check in</h1><p className="err">{fatal}</p><button onClick={onDone}>Back</button></main>;
  if (camError) return <main><h1>Camera needed</h1><p className="err">Allow camera access in your browser settings, then try again.</p><button onClick={onDone}>Back</button></main>;
  return <main>
    <div style={{ position: "fixed", inset: 0, zIndex: 10, display: phase === "face" ? "flex" : "none", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16, background: screen || "#fff", color: "#000", textAlign: "center", padding: 16 }}>
      <video ref={faceVideo} playsInline muted style={{ width: 180, height: 180, objectFit: "cover", borderRadius: "50%" }} />
      <p style={{ fontWeight: 600 }}>{noFace ? "Can't see your face. Hold the phone at face level in good light." : "Look at the screen and hold still"}</p>
      {noFace && <button className="secondary" onClick={() => { skipFace.current = true; }}>Continue without face check</button>}
    </div>
    <h2>{session.course_name}</h2>
    <video ref={video} playsInline muted style={{ transform: "none" }} />
    <p>{hint}</p><button className="secondary" onClick={onDone}>Cancel</button></main>;
}
