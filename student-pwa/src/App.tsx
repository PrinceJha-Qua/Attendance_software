import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, call, isSignedIn, json, login, raw, signOut } from "../../shared/api";
import Home from "./Home";
import { computeEmbedding, EMBEDDING_MODEL } from "./embedding";

const CONSENT_VERSION = "v1-2026-10";
type Enrollment = { consent_given: boolean; status: "none" | "pending" | "confirmed" | "rejected"; model: string | null };

export default function App() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  const [enr, setEnr] = useState<Enrollment | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setEnr(await call<Enrollment>("/students/me/enrollment")); setError(""); }
    catch (e) { if (e instanceof ApiError && e.status === 401) setSignedIn(false); else setError("Could not reach the server. Check your connection."); }
  }, []);

  useEffect(() => { if (signedIn) load(); }, [signedIn, load]);
  useEffect(() => {
    if (enr?.status !== "pending") return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [enr?.status, load]);

  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />;
  if (!enr) return <main><p className="muted">Loading… {error && <span className="err">{error}</span>}</p></main>;
  if (!enr.consent_given) return <Consent onDone={load} />;
  const outdated = enr.status !== "none" && enr.status !== "rejected" && enr.model !== EMBEDDING_MODEL;  // made by a placeholder/older model
  if (enr.status === "none" || enr.status === "rejected" || outdated) return <Selfie rejected={enr.status === "rejected"} outdated={outdated} onDone={load} />;
  const out = () => { signOut(); setSignedIn(false); setEnr(null); };
  if (enr.status === "confirmed") return <Home onChange={load} onSignOut={out} />;
  return <Status enr={enr} onChange={load} onSignOut={out} />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [id, setId] = useState(""); const [pw, setPw] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { if ((await login(id.trim(), pw)) !== "student") { signOut(); setErr("This app is for students."); } else onDone(); }
    catch (x) { setErr(x instanceof ApiError && x.status === 401 ? "Wrong ID or password." : "Could not sign in. Try again."); }
    setBusy(false);
  };
  return <main><h1>GateKeeper</h1>
    <form onSubmit={submit}>
      <input placeholder="Roll number or email" value={id} onChange={(e) => setId(e.target.value)} autoCapitalize="none" autoComplete="username" required />
      <input placeholder="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required />
      <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>{err && <p className="err">{err}</p>}</main>;
}

function Consent({ onDone }: { onDone: () => void }) {
  const [err, setErr] = useState("");
  const agree = async () => { try { await call("/students/me/consent", json("POST", { text_version: CONSENT_VERSION })); onDone(); } catch { setErr("Could not save. Try again."); } };
  return <main><h1>Before we start</h1>
    <p>To mark your attendance, GateKeeper takes <b>one reference photo of your face</b> now, with your professor checking your ID card.</p>
    <ul><li>The photo is stored encrypted and shown only to your professor, to verify attendance questions.</li>
      <li>During check-in, <b>no photo leaves your phone</b>: only a list of numbers computed on your phone is sent.</li>
      <li>You can delete your face data at any time; it is also deleted at the end of the term.</li></ul>
    <button onClick={agree}>I agree</button>{err && <p className="err">{err}</p>}</main>;
}

function Selfie({ rejected, outdated, onDone }: { rejected: boolean; outdated: boolean; onDone: () => void }) {
  const video = useRef<HTMLVideoElement>(null); const stream = useRef<MediaStream | null>(null);
  const [photo, setPhoto] = useState<Blob | null>(null); const [url, setUrl] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false); const [camOk, setCamOk] = useState(true);

  const start = useCallback(async () => {
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 640 } }, audio: false });
      if (video.current) { video.current.srcObject = stream.current; await video.current.play(); }
      setCamOk(true);
    } catch { setCamOk(false); }
  }, []);
  useEffect(() => { start(); return () => stream.current?.getTracks().forEach((t) => t.stop()); }, [start]);

  const setBlob = (b: Blob) => { setPhoto(b); setUrl(URL.createObjectURL(b)); stream.current?.getTracks().forEach((t) => t.stop()); };
  const capture = () => {
    const v = video.current!; const c = document.createElement("canvas"); const s = Math.min(1, 640 / Math.max(v.videoWidth, v.videoHeight));
    c.width = v.videoWidth * s; c.height = v.videoHeight * s; c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    c.toBlob((b) => b && setBlob(b), "image/jpeg", 0.85);
  };
  const retake = () => { setPhoto(null); setUrl(""); start(); };
  const submit = async () => {
    if (!photo) return; setBusy(true); setErr("");
    try {
      const vector = await computeEmbedding(photo);
      if (outdated) await call("/students/me/face-data", { method: "DELETE" });
      const fd = new FormData(); fd.append("photo", photo, "ref.jpg"); fd.append("vector", JSON.stringify(vector)); fd.append("model", EMBEDDING_MODEL);
      const r = await raw("/students/me/enroll", { method: "POST", body: fd });
      if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).error?.code ?? "FAILED");
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError && e.code === "PHOTO_TOO_LARGE" ? "Photo too large. Retake." : e instanceof Error && e.message === "NO_FACE" ? "No face found in the photo. Retake it facing the camera in good light." : "Upload failed. Try again.");
    }
    setBusy(false);
  };
  return <main><h1>Reference photo</h1>
    {rejected && <p className="err">Your last photo was not accepted. Please retake it.</p>}
    {outdated && <p className="err">Face check has been upgraded. Please retake your reference photo and show your ID again.</p>}
    <p className="muted">Face the camera in good light. Show your ID card to your professor when asked.</p>
    {url ? <img className="preview" src={url} alt="Your photo" /> : camOk ? <video ref={video} playsInline muted /> : (
      <div><p className="err">Camera unavailable. Allow camera access, or pick a photo instead.</p>
        <input type="file" accept="image/*" capture="user" onChange={(e) => e.target.files?.[0] && setBlob(e.target.files[0])} /></div>)}
    {!url && camOk && <button onClick={capture}>Take photo</button>}
    {url && <><button onClick={submit} disabled={busy}>{busy ? "Uploading…" : "Use this photo"}</button><button className="secondary" onClick={retake}>Retake</button></>}
    {err && <p className="err">{err}</p>}</main>;
}

function Status({ enr, onChange, onSignOut }: { enr: Enrollment; onChange: () => void; onSignOut: () => void }) {
  const del = async () => { if (confirm("Delete your face data? You will need to enroll again.")) { await call("/students/me/face-data", { method: "DELETE" }); onChange(); } };
  return <main><h1>{enr.status === "confirmed" ? "You are enrolled ✓" : "Waiting for your professor"}</h1>
    {enr.status === "pending" && <p>Show your ID card to your professor. This page updates by itself once they confirm.</p>}
    {enr.status === "confirmed" && <p className="ok">Check-in and your attendance dashboard arrive in the next updates.</p>}
    <button className="secondary" onClick={del}>Delete my face data</button>
    <button className="secondary" onClick={onSignOut}>Sign out</button></main>;
}
