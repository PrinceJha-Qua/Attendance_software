import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import qrcode from "qrcode-generator";
import { call } from "../../shared/api";
import { encodeBurst } from "../../shared/audiocodec";

type Batch = { step_ms: number; server_ms: number; tokens: { window: number; payload: string; audio_code?: number }[] };
type Status = "created" | "live" | "paused" | "closed";

// Shown on the projector: ONLY the rotating QR and a countdown. No student data ever appears here.
export default function Projection() {
  const { id } = useParams();
  const canvas = useRef<HTMLCanvasElement>(null);
  const store = useRef({ tokens: new Map<number, string>(), codes: new Map<number, number>(), audioMode: "off", amp: 0.25, ctx: null as AudioContext | null, scheduled: new Set<number>(), step: 1500, offset: 0, fetching: false, drawn: "", status: "created" as Status, pct: 70 });
  const [status, setStatus] = useState<Status>("created");
  const [online, setOnline] = useState(true);
  const [pct, setPct] = useState(70);
  const [barVisible, setBarVisible] = useState(true);
  const [audioMode, setAudioMode] = useState("off");
  const [soundOn, setSoundOn] = useState(false);
  const [vol, setVol] = useState(25);

  useEffect(() => { store.current.pct = pct; store.current.drawn = ""; }, [pct]);

  useEffect(() => {
    const st = store.current;
    const refill = async () => {
      if (st.fetching || st.status !== "live") return;
      st.fetching = true;
      try {
        const t0 = Date.now();
        const b = await call<Batch>(`/sessions/${id}/tokens`);
        const t1 = Date.now();
        st.step = b.step_ms; st.offset = b.server_ms + (t1 - t0) / 2 - t1;  // compensate for half the round trip
        for (const t of b.tokens) { st.tokens.set(t.window, t.payload); if (t.audio_code !== undefined) st.codes.set(t.window, t.audio_code); }
        for (const w of [...st.tokens.keys()]) if (w < Math.floor((Date.now() + st.offset) / st.step) - 3) st.tokens.delete(w);
        setOnline(true);
      } catch { setOnline(false); }
      st.fetching = false;
    };
    const poll = async () => {
      try { const s = await call<{ status: Status; audio_mode: string }>(`/sessions/${id}`); st.status = s.status; st.audioMode = s.audio_mode; setStatus(s.status); setAudioMode(s.audio_mode); } catch { setOnline(false); }
    };
    const tick = () => {
      const now = Date.now() + st.offset, w = Math.floor(now / st.step);
      const ahead = [...st.tokens.keys()].reduce((m, k) => Math.max(m, k), 0) - w;
      if (st.status === "live" && ahead * st.step < 30_000) refill();
    };
    poll().then(refill);
    const a = setInterval(poll, 2000), b = setInterval(tick, 1000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [id]);

  useEffect(() => { store.current.amp = vol / 100; }, [vol]);

  // Ultrasound beacon: one burst per QR window, scheduled on the audio clock from the server-aligned wall clock.
  const enableSound = async () => {
    const st = store.current;
    const ctx = new AudioContext({ sampleRate: 48000 });  // must be created from a click (autoplay policy)
    await ctx.resume(); st.ctx = ctx; setSoundOn(true);
  };
  useEffect(() => {
    const st = store.current;
    const t = setInterval(() => {
      const ctx = st.ctx;
      if (!ctx || st.status !== "live" || st.audioMode !== "burst") return;
      const nowSrv = Date.now() + st.offset, next = Math.floor(nowSrv / st.step) + 1;
      for (const w of [next, next + 1]) {
        const code = st.codes.get(w);
        if (code === undefined || st.scheduled.has(w)) continue;
        const samples = encodeBurst(ctx.sampleRate, w % 256, code, st.amp);
        const buf = ctx.createBuffer(1, samples.length, ctx.sampleRate); buf.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
        const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination);
        src.start(ctx.currentTime + Math.max(0, (w * st.step - nowSrv) / 1000));
        st.scheduled.add(w);
        for (const o of st.scheduled) if (o < next - 5) st.scheduled.delete(o);
      }
    }, 200);
    return () => { clearInterval(t); store.current.ctx?.close(); };
  }, []);

  useEffect(() => {  // draw loop
    let raf = 0;
    const draw = () => {
      const st = store.current, c = canvas.current;
      if (c) {
        const now = Date.now() + st.offset, w = Math.floor(now / st.step), payload = st.status === "live" ? st.tokens.get(w) : undefined;
        const ctx = c.getContext("2d")!;
        const key = payload ? `${payload}|${st.pct}|${innerWidth}x${innerHeight}` : "none";
        if (payload && key !== st.drawn) {
          const q = qrcode(0, "M"); q.addData(payload); q.make();
          const n = q.getModuleCount(), px = Math.max(1, Math.floor((Math.min(innerWidth, innerHeight) * st.pct / 100) / (n + 8))), size = px * (n + 8);
          c.width = c.height = size; ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, size, size); ctx.fillStyle = "#000";
          for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (q.isDark(r, k)) ctx.fillRect((k + 4) * px, (r + 4) * px, px, px);
          st.drawn = key;
        } else if (!payload && st.drawn !== "none") { ctx.clearRect(0, 0, c.width, c.height); c.width = c.height = 1; st.drawn = "none"; }
        const ring = document.getElementById("ring");
        if (ring) ring.style.width = `${((now % st.step) / st.step) * 100}%`;
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {  // keep the screen awake
    let lock: any = null;
    const get = async () => { try { lock = await (navigator as any).wakeLock?.request("screen"); } catch { /* unsupported */ } };
    get(); const vis = () => document.visibilityState === "visible" && get();
    document.addEventListener("visibilitychange", vis);
    return () => { document.removeEventListener("visibilitychange", vis); lock?.release?.(); };
  }, []);

  useEffect(() => {  // controls bar shows on mouse move, hides after 3 s; F toggles fullscreen
    let t: number; const show = () => { setBarVisible(true); clearTimeout(t); t = window.setTimeout(() => setBarVisible(false), 3000); };
    const key = (e: KeyboardEvent) => { if (e.key.toLowerCase() === "f") document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen(); };
    show(); window.addEventListener("mousemove", show); window.addEventListener("keydown", key);
    return () => { clearTimeout(t); window.removeEventListener("mousemove", show); window.removeEventListener("keydown", key); };
  }, []);

  return <div style={{ position: "fixed", inset: 0, background: "#fff", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
    <canvas ref={canvas} />
    {status !== "live" && <div style={{ fontSize: "5vmin", color: "#333" }}>{status === "paused" ? "Paused" : status === "closed" ? "Session closed" : "Waiting to start…"}</div>}
    {status === "live" && !online && <div style={{ position: "absolute", bottom: 40, fontSize: "3vmin", color: "#c0392b" }}>Reconnecting…</div>}
    <div style={{ position: "absolute", bottom: 0, left: 0, height: 6, width: "100%", background: "#eee" }}><div id="ring" style={{ height: 6, background: "#1b4dff", width: 0 }} /></div>
    {barVisible && <div style={{ position: "absolute", top: 8, left: 8, background: "#000a", color: "#fff", padding: "6px 10px", borderRadius: 8, font: "14px system-ui" }}>
      QR size <input type="range" min={30} max={100} value={pct} onChange={(e) => setPct(+e.target.value)} style={{ verticalAlign: "middle" }} /> · press F for fullscreen
      {audioMode === "burst" && <> · {soundOn ? <>beacon volume <input type="range" min={5} max={60} value={vol} onChange={(e) => setVol(+e.target.value)} style={{ verticalAlign: "middle" }} /></> : null}</>}</div>}
    {audioMode === "burst" && !soundOn && status === "live" && <button onClick={enableSound} style={{ position: "absolute", top: 8, right: 8, padding: "10px 16px", fontSize: 16 }}>Enable ultrasound beacon</button>}
    {audioMode === "burst" && soundOn && barVisible && <div style={{ position: "absolute", top: 8, right: 8, background: "#000a", color: "#fff", padding: "6px 10px", borderRadius: 8, font: "14px system-ui" }}>beacon on</div>}
  </div>;
}
