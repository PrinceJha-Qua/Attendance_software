import { useCallback, useEffect, useState } from "react";
import { ApiError, call, json } from "../../shared/api";

type Sum = { attended: number; conducted: number; required_pct: number; planned_total: number | null; pct: number | null; status: "none" | "safe" | "borderline" | "below";
  skip_now: number | null; can_skip_until_end: number | null; need_in_a_row: number | null; reachable: boolean | null };
type CourseRow = Sum & { course_id: string; name: string; professor: string };
type Hist = { record_id: string; date: string | null; status: string; source: string; note: string | null; dispute: { status: string } | null; can_dispute: boolean };

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "es"}`;
export function advice(s: Sum): string {
  if (s.status === "none") return "No classes recorded yet.";
  if (s.status === "below") {
    if (s.reachable === false) return `Reaching ${s.required_pct}% by the end of the term is no longer possible. Please talk to your professor.`;
    return s.need_in_a_row === null ? `You are below ${s.required_pct}%.` : `Attend the next ${plural(s.need_in_a_row, "class")} in a row to get back to ${s.required_pct}%.`;
  }
  if (s.can_skip_until_end !== null) return s.can_skip_until_end === 0 ? "You need to attend every remaining class." : `You can still miss ${plural(s.can_skip_until_end, "class")} before the end of the term.`;
  if (s.skip_now === null) return "";
  return s.skip_now === 0 ? "Do not miss the next class." : `You can miss ${plural(s.skip_now, "class")} and stay above ${s.required_pct}%.`;
}
const COLOUR = { none: "#666", safe: "var(--ok)", borderline: "#b9770e", below: "var(--err)" } as const;
const LABEL = { none: "No data", safe: "Safe", borderline: "Borderline", below: "Below" } as const;

export default function Dashboard() {
  const [rows, setRows] = useState<CourseRow[] | null>(null); const [open, setOpen] = useState<string | null>(null); const [err, setErr] = useState("");
  useEffect(() => { call<CourseRow[]>("/students/me/courses").then(setRows).catch(() => setErr("Could not load. Check your connection.")); }, []);
  if (open) return <CourseView id={open} onBack={() => setOpen(null)} />;
  return <div><h1>My attendance</h1>
    {rows === null ? <p className="muted">{err || "Loading…"}</p> : rows.length === 0 ? <p className="muted">You are not on any course roster yet.</p> :
      rows.map((c) => <div key={c.course_id} className="course" onClick={() => setOpen(c.course_id)}>
        <div className="row"><b>{c.name}</b><span style={{ color: COLOUR[c.status], fontWeight: 600 }}>{LABEL[c.status]}</span></div>
        <div className="big">{c.pct === null ? "–" : `${c.pct}%`}<span className="muted"> {c.attended}/{c.conducted} classes</span></div>
        <p>{advice(c)}</p></div>)}</div>;
}

function CourseView({ id, onBack }: { id: string; onBack: () => void }) {
  const [d, setD] = useState<{ course: { name: string }; summary: Sum; history: Hist[] } | null>(null); const [err, setErr] = useState("");
  const load = useCallback(() => call(`/students/me/courses/${id}/attendance`).then(setD).catch(() => setErr("Could not load.")), [id]);
  useEffect(() => { load(); }, [load]);
  const dispute = async (r: Hist) => {
    const note = prompt("Tell your professor what happened (for example: I was in row 3 but my phone could not check in)."); if (!note) return;
    try { await call(`/students/me/attendance/${r.record_id}/dispute`, json("POST", { note })); load(); } catch (e) { setErr(e instanceof ApiError && e.code === "VALIDATION_ERROR" ? "Please write a little more." : "Could not send. Try again."); }
  };
  if (!d) return <div><button className="secondary" onClick={onBack}>Back</button><p className="muted">{err || "Loading…"}</p></div>;
  return <div><button className="secondary" onClick={onBack}>Back</button><h1>{d.course.name}</h1>
    <div className="course"><div className="big">{d.summary.pct === null ? "–" : `${d.summary.pct}%`}<span className="muted"> need {d.summary.required_pct}%</span></div><p>{advice(d.summary)}</p></div>
    {err && <p className="err">{err}</p>}
    <table><tbody>{d.history.map((h) => <tr key={h.record_id}>
      <td>{h.date ? new Date(h.date).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "–"}</td>
      <td style={{ color: h.status === "absent" ? "var(--err)" : "var(--ok)" }}>{h.status === "present" ? "Present" : h.status === "absent" ? "Absent" : "Excused"}{h.source === "manual" && " (added by professor)"}{h.source === "resolved" && " (confirmed by professor)"}</td>
      <td>{h.dispute ? <span className="muted">Dispute {h.dispute.status}</span> : h.can_dispute && <button className="secondary small" onClick={() => dispute(h)}>I was there</button>}</td></tr>)}</tbody></table>
    {d.history.length === 0 && <p className="muted">No classes recorded yet.</p>}</div>;
}

export function Disputes() {
  const [rows, setRows] = useState<{ id: string; course: string; date: string | null; status: string; decided_note: string | null }[] | null>(null);
  useEffect(() => { call<any[]>("/students/me/disputes").then(setRows).catch(() => setRows([])); }, []);
  if (!rows || rows.length === 0) return null;
  return <><h3>My disputes</h3>{rows.map((r) => <p key={r.id}>{r.course}, {r.date ? new Date(r.date).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""}: <b>{r.status}</b>{r.decided_note && <span className="muted"> · {r.decided_note}</span>}</p>)}</>;
}
