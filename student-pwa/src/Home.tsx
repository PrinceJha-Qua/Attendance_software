import { useCallback, useEffect, useState } from "react";
import { call } from "../../shared/api";
import Scan, { ActiveSession } from "./Scan";
import Dashboard, { Disputes } from "./Dashboard";
import { warmFaces } from "./faces";
import { warmDecoder } from "./scanner";

type Item = ActiveSession & { id: string; course_name: string };

export default function Home({ onChange, onSignOut }: { onChange: () => void; onSignOut: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [scanning, setScanning] = useState<Item | null>(null);
  const [tab, setTab] = useState<"today" | "attendance" | "privacy">("today");
  const [err, setErr] = useState("");

  const load = useCallback(() => call<Item[]>("/sessions/active").then((x) => { setItems(x); setErr(""); }).catch(() => setErr("Could not reach the server.")), []);
  useEffect(() => { warmDecoder().catch(() => {}); warmFaces().catch(() => {}); }, []);  // download the QR decoder before it is needed
  useEffect(() => {
    load(); const t = setInterval(load, 5000);
    const vis = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [load]);

  if (scanning) return <Scan session={scanning} onDone={() => { setScanning(null); load(); }} />;
  const del = async () => { if (confirm("Delete your face data? You will need to enroll again.")) { await call("/students/me/face-data", { method: "DELETE" }); onChange(); } };
  const today = <><h1>Today</h1>
    {items === null ? <p className="muted">Loading…</p> : items.length === 0 ? <p className="muted">No class is live right now. This page checks automatically.</p> :
      items.map((s) => <div key={s.id}><h2>{s.course_name}</h2>
        {s.checked_in ? <p className="ok">Checked in ✓</p> : s.status === "paused" ? <p className="muted">Class is paused</p> : <button onClick={() => setScanning(s)}>Check in</button>}</div>)}
    {err && <p className="err">{err}</p>}</>;
  const privacy = <><h1>Privacy</h1>
    <p>What GateKeeper keeps about you:</p>
    <ul><li>One reference photo of your face, stored encrypted, shown only to your professor when they need to check attendance in person.</li>
      <li>A list of numbers computed from that photo, used to match your face at check-in.</li>
      <li>During check-in, no photo or sound leaves your phone: only numbers.</li>
      <li>Your attendance records for each class.</li></ul>
    <p>Your face data is deleted at the end of the term, or whenever you ask.</p>
    <button className="secondary" onClick={del}>Delete my face data</button><button className="secondary" onClick={onSignOut}>Sign out</button></>;
  return <main style={{ paddingBottom: 80 }}>
    {tab === "today" && today}{tab === "attendance" && <><Dashboard /><Disputes /></>}{tab === "privacy" && privacy}
    <nav className="tabs">{(["today", "attendance", "privacy"] as const).map((t) => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "today" ? "Today" : t === "attendance" ? "Attendance" : "Privacy"}</button>)}</nav></main>;
}
