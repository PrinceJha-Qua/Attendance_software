import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ApiError, call, isSignedIn, json, login, raw, signOut } from "../../shared/api";
import Projection from "./Projection";
import Reports from "./Reports";
import Session from "./Session";

type Course = { id: string; name: string; planned_total_sessions: number | null; required_pct: number; roster_count: number; confirmed_count: number };

export default function App() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />;
  const out = () => { signOut(); setSignedIn(false); };
  return <BrowserRouter><Routes>
    <Route path="/project/:id" element={<Projection />} />
    <Route path="*" element={<main>
      <div className="bar"><Link to="/"><h1>GateKeeper</h1></Link><button className="secondary" onClick={out}>Sign out</button></div>
      <Routes><Route path="/" element={<Courses />} /><Route path="/courses/:id" element={<CourseDetail />} /><Route path="/session/:id" element={<Session />} /><Route path="/courses/:id/report" element={<Reports />} /></Routes></main>} />
  </Routes></BrowserRouter>;
}

function Login({ onDone }: { onDone: () => void }) {
  const [id, setId] = useState(""); const [pw, setPw] = useState(""); const [err, setErr] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    try { if ((await login(id.trim(), pw)) !== "professor") { signOut(); setErr("Professor accounts only."); } else onDone(); }
    catch { setErr("Sign-in failed."); }
  };
  return <main><h1>GateKeeper: Professor</h1><form onSubmit={submit}>
    <input placeholder="Email" value={id} onChange={(e) => setId(e.target.value)} required />
    <input placeholder="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} required />
    <button>Sign in</button></form>{err && <p className="err">{err}</p>}</main>;
}

function Courses() {
  const [courses, setCourses] = useState<Course[]>([]); const [name, setName] = useState(""); const [err, setErr] = useState("");
  const load = useCallback(() => call<Course[]>("/courses").then(setCourses).catch(() => setErr("Could not load courses.")), []);
  useEffect(() => { load(); }, [load]);
  const create = async (e: React.FormEvent) => { e.preventDefault(); try { await call("/courses", json("POST", { name })); setName(""); load(); } catch { setErr("Could not create course."); } };
  return <><h2>Your courses</h2>
    <table><thead><tr><th>Course</th><th>Roster</th><th>Enrolled</th></tr></thead><tbody>
      {courses.map((c) => <tr key={c.id}><td><Link to={`/courses/${c.id}`}>{c.name}</Link></td><td>{c.roster_count}</td><td>{c.confirmed_count}</td></tr>)}</tbody></table>
    <form onSubmit={create}><input placeholder="New course name" value={name} onChange={(e) => setName(e.target.value)} required /><button>Create</button></form>
    {err && <p className="err">{err}</p>}</>;
}

type Created = { roll_no: string; email: string; initial_password: string };
type RosterRow = { student_id: string; name: string; roll_no: string; email: string; enrollment: string };
type Pending = { student_id: string; name: string; roll_no: string; photo_url: string };

function CourseDetail() {
  const { id } = useParams(); const nav = useNavigate();
  const [course, setCourse] = useState<Course | null>(null); const [roster, setRoster] = useState<RosterRow[]>([]); const [pending, setPending] = useState<Pending[]>([]);
  const [created, setCreated] = useState<Created[]>([]); const [msg, setMsg] = useState(""); const [req, setReq] = useState(""); const [planned, setPlanned] = useState("");

  const load = useCallback(async () => {
    try {
      const cs = await call<Course[]>("/courses"); const c = cs.find((x) => x.id === id);
      if (!c) return nav("/"); setCourse(c); setReq(String(c.required_pct)); setPlanned(c.planned_total_sessions ? String(c.planned_total_sessions) : "");
      setRoster(await call(`/courses/${id}/roster`)); setPending(await call(`/courses/${id}/enrollments/pending`));
    } catch (e) { setMsg(e instanceof ApiError ? e.code : "Load failed"); }
  }, [id, nav]);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const save = async () => { try { await call(`/courses/${id}`, json("PATCH", { required_pct: Number(req), planned_total_sessions: planned ? Number(planned) : null })); setMsg("Saved."); load(); } catch { setMsg("Invalid settings."); } };
  const upload = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    const r = await raw(`/courses/${id}/roster/import`, { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) return setMsg(`Import failed: ${j.error?.code}`);
    setCreated(j.created_accounts); setMsg(`Added ${j.added_to_roster}, already on roster ${j.already_on_roster}, errors ${j.errors.length}${j.errors.length ? " (rows " + j.errors.map((e: any) => e.row).join(", ") + ")" : ""}.`); load();
  };
  const download = () => {
    const csv = "roll_no,email,initial_password\n" + created.map((c) => `${c.roll_no},${c.email},${c.initial_password}`).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "new_student_logins.csv"; a.click();
  };
  const decide = async (sid: string, what: "confirm" | "reject") => { try { await call(`/courses/${id}/enrollments/${sid}/${what}`, { method: "POST" }); load(); } catch { setMsg("Could not update."); } };

  const [beacon, setBeacon] = useState(true);
  const startSession = async (step_ms: number) => { try { const s = await call<{ id: string }>("/sessions", json("POST", { course_id: id, step_ms, audio_mode: beacon ? "burst" : "off" })); nav(`/session/${s.id}`); } catch (e) { if (e instanceof ApiError && e.code === "OPEN_SESSION_EXISTS") { const r = await call<{ open_session_id: string | null }>(`/courses/${id}/report`).catch(() => null); if (r?.open_session_id && confirm("This course already has an open session. Go to it? (Close it first to start a new one.)")) nav(`/session/${r.open_session_id}`); else setMsg("This course already has an open session. Close it first."); } else setMsg("Could not create session."); } };

  if (!course) return <p className="muted">Loading…</p>;
  return <><h2>{course.name} <Link to={`/courses/${id}/report`} style={{ fontSize: 15 }}>Report &amp; disputes →</Link></h2>
    <p><b>New session</b> (QR changes every): {[1500, 3000, 5000].map((ms) => <button key={ms} onClick={() => startSession(ms)}>{ms / 1000}s</button>)} <span className="muted">Use 3s or 5s in big halls.</span> <label><input type="checkbox" checked={beacon} onChange={(e) => setBeacon(e.target.checked)} style={{ width: "auto" }} /> ultrasound beacon</label></p>
    <p>Required attendance % <input style={{ width: 70 }} value={req} onChange={(e) => setReq(e.target.value)} /> Planned sessions <input style={{ width: 70 }} value={planned} onChange={(e) => setPlanned(e.target.value)} /> <button onClick={save}>Save</button></p>
    {msg && <p className="muted">{msg}</p>}
    <h3>Roster ({roster.length})</h3>
    <p>Upload CSV with columns <code>roll_no,name,email</code>: <input type="file" accept=".csv" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></p>
    {created.length > 0 && <p className="err">{created.length} new student accounts created. Their passwords are shown only once: <button onClick={download}>Download logins CSV</button></p>}
    <h3>Pending enrollments ({pending.length})</h3>
    <p className="muted">Check the student's ID card in person before confirming.</p>
    {pending.map((p) => <div className="card" key={p.student_id}><Photo url={p.photo_url} /><div>{p.name}<br /><span className="muted">{p.roll_no}</span></div>
      <button onClick={() => decide(p.student_id, "confirm")}>Confirm</button><button className="danger" onClick={() => decide(p.student_id, "reject")}>Reject</button></div>)}
    <table><thead><tr><th>Roll</th><th>Name</th><th>Email</th><th>Enrollment</th></tr></thead><tbody>
      {roster.map((r) => <tr key={r.student_id}><td>{r.roll_no}</td><td>{r.name}</td><td>{r.email}</td><td>{r.enrollment}</td></tr>)}</tbody></table></>;
}

// Photos need the auth header, so fetch as a blob instead of using <img src> directly.
function Photo({ url }: { url: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let u = ""; raw(url.replace("/api/v1", "")).then((r) => r.ok ? r.blob() : null).then((b) => { if (b) { u = URL.createObjectURL(b); setSrc(u); } });
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [url]);
  return <img src={src} alt="" />;
}
