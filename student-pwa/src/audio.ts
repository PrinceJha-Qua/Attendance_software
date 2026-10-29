import { BurstDecoder } from "../../shared/audiocodec";

// Microphone listener for the ultrasonic beacon. Audio never leaves the phone: only (window mod 256, code, snr) is sent.
// Browser audio processing is switched off, since echo cancellation and noise suppression remove exactly this band.
export type AudioReport = { status: "heard" | "none_heard" | "denied" | "unsupported"; window_low8?: number; code?: number; snr_db?: number };
export type Listener = { report(maxAgeMs: number): AudioReport; info: Record<string, unknown>; stats(): Record<string, unknown>; stop(): void };

const WORKLET = `class Tap extends AudioWorkletProcessor { process(inputs) { const c = inputs[0][0]; if (c) this.port.postMessage(c.slice(0)); return true; } }
registerProcessor("tap", Tap);`;

export async function startListener(): Promise<Listener | { error: "denied" | "unsupported" }> {
  if (!navigator.mediaDevices?.getUserMedia || !(window.AudioContext || (window as any).webkitAudioContext)) return { error: "unsupported" };
  let stream: MediaStream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 }, video: false }); }
  catch (e) { return { error: (e as DOMException).name === "NotAllowedError" ? "denied" : "unsupported" }; }

  const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
  await ctx.resume().catch(() => {});
  const dec = new BurstDecoder(ctx.sampleRate);
  let latest: { low8: number; code: number; snr: number; at: number } | null = null; let frames = 0;
  const feed = (b: Float32Array) => { frames++; for (const d of dec.push(b)) latest = { low8: d.low8, code: d.code16, snr: d.snrDb, at: Date.now() }; };
  const src = ctx.createMediaStreamSource(stream);
  let worklet = true; let node: AudioNode;
  try {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(url); URL.revokeObjectURL(url);
    const n = new AudioWorkletNode(ctx, "tap", { numberOfOutputs: 0 }); n.port.onmessage = (e) => feed(e.data); node = n;
  } catch {  // old Safari: fall back to the deprecated main-thread node
    worklet = false;
    const n = ctx.createScriptProcessor(2048, 1, 1); n.onaudioprocess = (e) => feed(e.inputBuffer.getChannelData(0).slice(0));
    n.connect(ctx.destination); node = n;
  }
  src.connect(node);
  const track = stream.getAudioTracks()[0];
  const info = { sampleRate: ctx.sampleRate, worklet, settings: track?.getSettings?.() ?? {} };
  return {
    info,
    report: (maxAgeMs) => latest && Date.now() - latest.at <= maxAgeMs ? { status: "heard", window_low8: latest.low8, code: latest.code, snr_db: Math.round(latest.snr * 10) / 10 } : { status: "none_heard" },
    stats: () => ({ frames, maxNorm: Math.round(dec.maxNorm * 1000) / 1000, lastHeardAgeMs: latest ? Date.now() - latest.at : null }),
    stop: () => { try { src.disconnect(); node.disconnect(); } catch { /* already gone */ } stream.getTracks().forEach((t) => t.stop()); ctx.close().catch(() => {}); },
  };
}
