import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { accessToken, ApiError, call, json, raw } from "../../shared/api";

export type SessionInfo = { id: string; course_id: string; status: "created" | "live" | "paused" | "closed"; step_ms: number; roster_count: number; checked_in_count: number };
type Row = { student_id: string; name: string; roll_no: string; display: string; final: string; source: string; note: string | null; checkin: { status: string; flags: string[] } | null };
type Summary = { roster: number; present: number; checked_in: number; review: number; not_here: number; audit_sampled: number; audit_not_seen: number };
type Item = { student_id: string; name: string; roll_no: string; kind: "flagged" | "overdue" | "audit"; reasons: string[]; photo_url: string | null };
type Tick = { checked_in: number; review: number; roster: number; rate_per_s: number; p50_ms: number | null; p95_ms: number | null };

const LABEL: Record<string, string> = { checked_in: "Checked in", review: "Needs a look", not_here: "Not here yet", present: "Present", absent: "Absent", excused: "Excused" };

// Private professor screen: roster status, Attention Panel with ID photos. It lives on /session/:id, never on the projected /project/:id route.
export default function Session() {
  const { id } = useParams();
  const [s, setS] = useState<SessionInfo | null>(null); const [err, setErr] = useState("");
  const [rows, setRows] = useState<Row[]>([]); const [sum, setSum] = useState<Summary | null>(null);
  const [items, setItems] = useState<Item[]>([]); const [open, setOpen] = useState(0);
  const [tick, setTick] = useState<Tick | null>(null); const [live, setLive] = useState(false);
  const [filter, setFilter] = useState("all");
  const seq = useRef(0); const timer = useRef<number | null>(null);

  const loadSession = useCallback(() => call<SessionInfo>(`/sessions/${id}`).then(setS).catch(() => setErr("Could not load session.")), [id]);
  const loadLists = useCallback(async () => {
    try {
      const [a, t] = await Promise.all([call<{ students: Row[]; summary: Summary }>(`/sessions/${id}/attendance`), call<{ items: Item[]; total_open: number }>(`/sessions/${id}/attention`)]);
      setRows(a.students); setSum(a.summary); setItems(t.items); setOpen(t.total_open); setErr("");
    } catch { setErr("Could not refresh. Retrying…"); }
  }, [id]);
  const soon = useCallback(() => { if (timer.current) return; timer.current = window.setTimeout(() => { timer.current = null; loadLists(); loadSession(); }, 250); }, [loadLists, loadSession]);

  useEffect(() => { loadSession(); loadLists(); const a = setInterval(loadLists, 10000), b = setInterval(loadSession, 5000); return () => { clearInterval(a); clearInterval(b); }; }, [loadSession, loadLists]);

  useEffect(() => {  // live feed with reconnect + replay from the last seen sequence number
    let ws: WebSocket | null = null; let stop = false; let retry = 1000; let t: number;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/v1/ws/sessions/${id}?token=${encodeURIComponent(accessToken() ?? "")}&since_seq=${seq.current}`);
      ws.onopen = () => { setLive(true); retry = 1000; };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.seq) seq.current = Math.max(seq.current, ev.seq);
        if (ev.type === "hello") { if (ev.data.resync) soon(); setTick(ev.data); }
        else if (ev.type === "stats.tick") setTick(ev.data);
        else if (ev.type !== "pong") soon();   // any change: refetch the lists (keeps photo URLs fresh, one source of truth)
      };
      ws.onclose = () => { setLive(false); if (!stop) { t = window.setTimeout(connect, retry); retry = Math.min(retry * 2, 10000); } };
    };
    connect();
    return () => { stop = true; clearTimeout(t); ws?.close(); };
  }, [id, soon]);

  const act = async (what: "start" | "pause" | "resume" | "close") => {
    if (what === "close" && !confirm("Close this session? Attendance records are written now. Unreviewed flags count as present.")) return;
    try { setS(await call<SessionInfo>(`/sessions/${id}/${what}`, { method: "POST" })); setErr(""); loadLists(); } catch (e) { setErr(e instanceof ApiError ? e.code : "Failed"); }
  };
  const verdict = async (sid: string, result: "seen" | "not_seen" | "unsure") => { try { await call(`/sessions/${id}/spotcheck/${sid}`, json("POST", { result })); loadLists(); } catch (e) { setErr(e instanceof ApiError ? e.code : "Failed"); } };
  const resolve = async (sid: string, status: "present" | "absent") => { try { await call(`/sessions/${id}/attendance/${sid}/resolve`, json("POST", { status })); loadLists(); } catch (e) { setErr(e instanceof ApiError ? e.code : "Failed"); } };
  const addManual = async (r: Row) => {
    const reason = prompt(`Why is ${r.name} added by hand? (for example: phone broken, no consent)`); if (!reason) return;
    try { await call(`/sessions/${id}/attendance/manual`, json("POST", { student_id: r.student_id, reason })); loadLists(); } catch (e) { setErr(e instanceof ApiError ? e.code : "Failed"); }
  };
  const exportCsv = async () => {
    const r = await raw(`/sessions/${id}/export?format=csv`); if (!r.ok) return setErr("Export failed");
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = `attendance_${id}.csv`; a.click();
  };

  if (!s) return <p className="muted">{err || "Loading…"}</p>;
  const running = s.status === "live" || s.status === "paused";
  const review = rows.filter((r) => r.display === "review");
  const shown = rows.filter((r) => filter === "all" || r.display === filter);
  return <>
    <div className="bar"><h2>Session <span className={live ? "live-dot" : "off-dot"} title={live ? "Live feed connected" : "Reconnecting…"}>●</span></h2><Link to={`/courses/${s.course_id}`}>Course</Link></div>
    <p>Status: <b>{s.status}</b> · QR changes every {s.step_ms / 1000}s</p>
    {s.status === "created" && <button onClick={() => act("start")}>Start session</button>}
    {s.status === "live" && <button className="secondary" onClick={() => act("pause")}>Pause</button>}
    {s.status === "paused" && <button onClick={() => act("resume")}>Resume</button>}
    {running && <button className="danger" onClick={() => act("close")}>Close session</button>}
    {running && <a href={`/project/${s.id}`} target="_blank" rel="noreferrer"><button>Open projection screen</button></a>}
    <button className="secondary" onClick={exportCsv}>Export CSV</button>
    {running && <span className="muted"> Drag the projection window to the projector, press F. Keep THIS screen private: it shows student photos.</span>}
    {err && <p className="err">{err}</p>}

    <div className="stats">
      <div className="stat"><b>{tick?.checked_in ?? s.checked_in_count} / {tick?.roster ?? s.roster_count}</b>checked in</div>
      <div className="stat"><b>{tick?.review ?? sum?.review ?? 0}</b>need a look</div>
      <div className="stat"><b>{tick?.rate_per_s ?? 0}/s</b>check-in rate</div>
      <div className="stat"><b>{tick?.p95_ms ?? "–"} ms</b>p95 server time</div>
    </div>

    {(items.length > 0 || running) && <div className="panel"><h3>Please verify in person {open > items.length && <span className="muted">({open - items.length} more waiting)</span>}</h3>
      {items.length === 0 ? <p className="muted">Nobody needs a look right now.</p> : <p className="muted">Call out these names and look. A flag is only a reason to look, nothing is recorded until you choose.</p>}
      {items.map((i) => <div className="attn" key={i.student_id}><Photo key={i.student_id} id={i.student_id} url={i.photo_url} />
        <div><b>{i.name}</b><br /><span className="muted">{i.roll_no}</span></div>
        <ul>{i.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <button onClick={() => verdict(i.student_id, "seen")}>Seen</button><button className="danger" onClick={() => verdict(i.student_id, "not_seen")}>Not here</button><button className="secondary" onClick={() => verdict(i.student_id, "unsure")}>Unsure</button>
      </div>)}</div>}

    {review.length > 0 && <><h3>Review queue ({review.length})</h3>
      <table><tbody>{review.map((r) => <tr key={r.student_id}><td>{r.roll_no}</td><td>{r.name}</td><td className="muted">{r.checkin?.flags.join(", ")}</td>
        <td><button onClick={() => resolve(r.student_id, "present")}>Present</button><button className="danger" onClick={() => resolve(r.student_id, "absent")}>Absent</button></td></tr>)}</tbody></table></>}

    <h3>Roster {sum && <span className="muted">· present now {sum.present}/{sum.roster}{sum.audit_sampled ? ` · random checks ${sum.audit_sampled}, not seen ${sum.audit_not_seen}` : ""}</span>}</h3>
    <p>Show: <select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">everyone</option>{Object.entries(LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></p>
    <table><thead><tr><th>Roll</th><th>Name</th><th>Status</th><th></th></tr></thead><tbody>
      {shown.map((r) => <tr key={r.student_id}><td>{r.roll_no}</td><td>{r.name}</td><td className={`s-${r.display}`}>{LABEL[r.display] ?? r.display}{r.source === "manual" && " (added by hand)"}{r.note && r.source !== "checkin" ? ` · ${r.note}` : ""}</td>
        <td>{r.display === "not_here" && s.status !== "created" && <button className="secondary" onClick={() => addManual(r)}>Add by hand</button>}</td></tr>)}</tbody></table>
  </>;
}

// Photos need the auth header, so they are fetched as blobs and released when the card goes away. Never cached by the page.
function Photo({ id, url }: { id: string; url: string | null }) {
  const [src, setSrc] = useState(""); const first = useRef(url);  // fetched once per student: refreshed signed URLs must not re-download (each view is audit-logged)
  useEffect(() => {
    const url0 = first.current; if (!url0) return; let u = ""; let dead = false;
    raw(url0.replace("/api/v1", "")).then((r) => r.ok ? r.blob() : null).then((b) => { if (b && !dead) { u = URL.createObjectURL(b); setSrc(u); } });
    return () => { dead = true; if (u) URL.revokeObjectURL(u); };
  }, [id]);
  return src ? <img src={src} alt="" /> : <div style={{ height: 210, background: "#eee", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center" }} className="muted">{url ? "Loading photo…" : "No photo"}</div>;
}
