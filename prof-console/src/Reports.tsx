import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, call, json, raw } from "../../shared/api";

type Stu = { student_id: string; roll_no: string; name: string; attended: number; conducted: number; pct: number | null; status: string; need_in_a_row: number | null; unreviewed_flags: number; open_disputes: number };
type Report = { course: { name: string; required_pct: number }; sessions_conducted: number; open_session_id: string | null; students: Stu[] };
type Disp = { id: string; student: { name: string; roll_no: string }; date: string | null; record_note: string | null; record_source: string; note: string; status: string; decided_note: string | null };

// Course report and disputes inbox (private professor screen).
export default function Reports() {
  const { id } = useParams();
  const [r, setR] = useState<Report | null>(null); const [ds, setDs] = useState<Disp[]>([]); const [all, setAll] = useState(false); const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try { const [a, b] = await Promise.all([call<Report>(`/courses/${id}/report`), call<Disp[]>(`/courses/${id}/disputes?status=${all ? "all" : "open"}`)]); setR(a); setDs(b); setErr(""); }
    catch { setErr("Could not load the report."); }
  }, [id, all]);
  useEffect(() => { load(); }, [load]);
  const decide = async (d: Disp, decision: "accept" | "reject") => {
    const note = prompt(decision === "accept" ? "Optional note to the student" : "Why is this rejected? (the student will see it)") ?? undefined; if (note === undefined && decision === "reject") return;
    try { await call(`/disputes/${d.id}/decide`, json("POST", { decision, note: note || null })); load(); } catch (e) { setErr(e instanceof ApiError ? e.code : "Failed"); }
  };
  const csv = () => {
    if (!r) return;
    const cell = (v: unknown) => { const t = String(v ?? ""); return `"${(/^[=+\-@\t\r]/.test(t) ? "'" + t : t).replace(/"/g, '""')}"`; };
    const body = ["roll_no,name,attended,conducted,percent,status", ...r.students.map((s) => [s.roll_no, s.name, s.attended, s.conducted, s.pct ?? "", s.status].map(cell).join(","))].join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([body], { type: "text/csv" })); a.download = `course_report_${id}.csv`; a.click();
  };
  if (!r) return <p className="muted">{err || "Loading…"}</p>;
  return <>
    <div className="bar"><h2>{r.course.name}: report</h2><Link to={`/courses/${id}`}>Back to course</Link></div>
    <p>{r.sessions_conducted} classes conducted · required {r.course.required_pct}% {r.open_session_id && <Link to={`/session/${r.open_session_id}`}>· open session →</Link>} <button className="secondary" onClick={csv}>Download CSV</button></p>
    {err && <p className="err">{err}</p>}
    <h3>Disputes {ds.filter((d) => d.status === "open").length > 0 && `(${ds.filter((d) => d.status === "open").length} open)`} <label className="muted"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} style={{ width: "auto" }} /> show decided</label></h3>
    {ds.length === 0 && <p className="muted">No {all ? "" : "open "}disputes.</p>}
    {ds.map((d) => <div className="panel" key={d.id}><b>{d.student.name}</b> <span className="muted">{d.student.roll_no} · {d.date ? new Date(d.date).toLocaleDateString() : ""} · marked absent ({d.record_source === "resolved" ? "by you" : "automatically"}{d.record_note ? `: ${d.record_note}` : ""})</span>
      <p>"{d.note}"</p>
      {d.status === "open" ? <><button onClick={() => decide(d, "accept")}>Accept (mark present)</button><button className="danger" onClick={() => decide(d, "reject")}>Reject</button></> : <p className="muted">{d.status}{d.decided_note ? `: ${d.decided_note}` : ""}</p>}</div>)}
    <h3>Students</h3>
    <table><thead><tr><th>Roll</th><th>Name</th><th>Attended</th><th>%</th><th>Status</th><th>Unreviewed flags</th></tr></thead><tbody>
      {[...r.students].sort((a, b) => (a.pct ?? 101) - (b.pct ?? 101)).map((s) => <tr key={s.student_id}><td>{s.roll_no}</td><td>{s.name}</td><td>{s.attended}/{s.conducted}</td><td>{s.pct ?? "–"}</td>
        <td className={s.status === "below" ? "err" : s.status === "borderline" ? "s-review" : ""}>{s.status}{s.status === "below" && s.need_in_a_row !== null ? ` (needs ${s.need_in_a_row} in a row)` : ""}</td><td>{s.unreviewed_flags || ""}</td></tr>)}</tbody></table>
  </>;
}
