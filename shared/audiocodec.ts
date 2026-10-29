// Ultrasonic burst codec shared by the professor emitter and the student receiver. Pure maths, no DOM, so it is testable in Node.
// Design (reports/Ultrasonic attendance audio reliability.md): a slow up-chirp preamble, then 32 bits sent as chirp keying
// (bit 1 = fast up-chirp, bit 0 = fast down-chirp) inside 17.5-19.0 kHz. Chirps are detected with a matched filter on complex
// baseband, so the receiver needs no carrier phase and tolerates the loud, uneven mic response of phones.
// Payload: window mod 256 (8 bits) + code16 (16 bits, server HMAC of the window) + CRC-8.
export const CENTER_HZ = 18250;
export const SWEEP_HZ = 1500;        // 17.5 .. 19.0 kHz
export const PREAMBLE_MS = 40;
export const BIT_MS = 20;
export const NBITS = 32;
export const RAMP_MS = 2;
export const BURST_MS = PREAMBLE_MS + NBITS * BIT_MS;  // 680 ms

export function crc8(bytes: number[]): number {
  let c = 0;
  for (const b of bytes) { c ^= b; for (let i = 0; i < 8; i++) c = c & 0x80 ? ((c << 1) ^ 0x07) & 0xff : (c << 1) & 0xff; }
  return c;
}
export function frameBits(low8: number, code16: number): number[] {
  const bytes = [low8 & 0xff, (code16 >> 8) & 0xff, code16 & 0xff];
  const all = [...bytes, crc8(bytes)];
  return all.flatMap((b) => Array.from({ length: 8 }, (_, i) => (b >> (7 - i)) & 1));
}
function bitsToFrame(bits: number[]): { low8: number; code16: number } | null {
  const bytes = [0, 1, 2, 3].map((k) => bits.slice(k * 8, k * 8 + 8).reduce((a, b) => (a << 1) | b, 0));
  return crc8(bytes.slice(0, 3)) === bytes[3] ? { low8: bytes[0], code16: (bytes[1] << 8) | bytes[2] } : null;
}

// baseband chirp: instantaneous frequency sweeps -SWEEP/2 .. +SWEEP/2 (up) or the reverse (down) over durMs
function chirpPhase(t: number, dur: number, up: boolean): number {
  const k = SWEEP_HZ / dur;
  return 2 * Math.PI * ((up ? -SWEEP_HZ / 2 : SWEEP_HZ / 2) * t + (up ? 0.5 : -0.5) * k * t * t);
}

/** One burst as passband samples at sampleRate. amp ~0.2-0.3 keeps it quiet and clean of speaker distortion. */
export function encodeBurst(fs: number, low8: number, code16: number, amp = 0.25): Float32Array {
  const segs: { dur: number; up: boolean }[] = [{ dur: PREAMBLE_MS, up: true }, ...frameBits(low8, code16).map((b) => ({ dur: BIT_MS, up: b === 1 }))];
  const total = Math.round((BURST_MS / 1000) * fs);
  const out = new Float32Array(total);
  const rampN = Math.round((RAMP_MS / 1000) * fs);
  let n0 = 0;
  for (const s of segs) {
    const n = Math.round((s.dur / 1000) * fs);
    for (let i = 0; i < n && n0 + i < total; i++) {
      const t = i / fs;
      const env = Math.min(1, i / rampN, (n - 1 - i) / rampN);
      out[n0 + i] = amp * Math.max(0, env) * Math.cos(2 * Math.PI * CENTER_HZ * ((n0 + i) / fs) + chirpPhase(t, s.dur / 1000, s.up));
    }
    n0 += n;
  }
  return out;
}

export type Decoded = { low8: number; code16: number; snrDb: number; atSample: number };

/** Streaming receiver: feed raw mic samples in any block size; returns frames whose CRC checks out. */
export class BurstDecoder {
  private D: number; private fsb: number; private L: number; private B: number; private Bf: number;
  private tplPre: Float32Array; private tplUp: Float32Array; private tplDn: Float32Array;  // interleaved complex, already conjugated
  private ph = 0; private dI = 0; private dQ = 0; private dN = 0;
  private buf: Float32Array; private cap: number; private n = 0;  // baseband ring (interleaved), n = baseband samples seen
  private energy: Float32Array; private preE: number;
  private run: { best: number; at: number; since: number } | null = null;
  private pending: { end: number; peak: number } | null = null;
  public threshold = 0.45;
  public maxNorm = 0;  // strongest preamble correlation seen since last reset: a cheap 'did anything chirp-like arrive' number for field trials
  private sampleCount = 0;

  constructor(private fs: number) {
    this.D = Math.max(1, Math.round(fs / 3000)); this.fsb = fs / this.D;
    this.L = Math.round((PREAMBLE_MS / 1000) * this.fsb); this.Bf = (BIT_MS / 1000) * this.fsb; this.B = Math.round(this.Bf);  // Bf is fractional at 44.1 kHz; slot starts use it so they do not drift
    this.cap = Math.round(this.fsb * 6); this.buf = new Float32Array(this.cap * 2); this.energy = new Float32Array(this.cap);
    const tpl = (len: number, dur: number, up: boolean) => {
      const t = new Float32Array(len * 2);
      for (let i = 0; i < len; i++) { const p = chirpPhase(i / this.fsb, dur / 1000, up); t[2 * i] = Math.cos(p); t[2 * i + 1] = -Math.sin(p); }
      return t;
    };
    this.tplPre = tpl(this.L, PREAMBLE_MS, true); this.tplUp = tpl(this.B, BIT_MS, true); this.tplDn = tpl(this.B, BIT_MS, false);
    this.preE = this.L;  // sum |tpl|^2
  }

  push(x: Float32Array): Decoded[] {
    const out: Decoded[] = [];
    const w = (2 * Math.PI * CENTER_HZ) / this.fs;
    for (let i = 0; i < x.length; i++) {
      const s = x[i];
      this.dI += s * Math.cos(this.ph); this.dQ -= s * Math.sin(this.ph);
      this.ph += w; if (this.ph > 2 * Math.PI) this.ph -= 2 * Math.PI;
      this.sampleCount++;
      if (++this.dN === this.D) {
        const r = this.baseband(this.dI / this.D * 2, this.dQ / this.D * 2);
        this.dI = this.dQ = 0; this.dN = 0;
        if (r) out.push(r);
      }
    }
    return out;
  }

  private at(k: number) { const j = (k % this.cap) * 2; return [this.buf[j], this.buf[j + 1]] as const; }

  private corr(tpl: Float32Array, len: number, start: number): { mag: number; e: number } {
    let re = 0, im = 0, e = 0;
    for (let i = 0; i < len; i++) {
      const j = ((start + i) % this.cap) * 2, zr = this.buf[j], zi = this.buf[j + 1];
      re += zr * tpl[2 * i] - zi * tpl[2 * i + 1]; im += zr * tpl[2 * i + 1] + zi * tpl[2 * i]; e += zr * zr + zi * zi;
    }
    return { mag: Math.hypot(re, im), e };
  }

  private baseband(i: number, q: number): Decoded | null {
    const j = (this.n % this.cap) * 2; this.buf[j] = i; this.buf[j + 1] = q; this.n++;
    const idx = this.n - 1;
    if (this.pending) {
      if (idx >= this.pending.end + Math.ceil(NBITS * this.Bf) + 1) {
        const p = this.pending; this.pending = null; this.run = null;
        return this.readBits(p.end + 1, p.peak);
      }
      return null;
    }
    if (this.n < this.L) return null;
    const { mag, e } = this.corr(this.tplPre, this.L, this.n - this.L);
    const norm = e > 1e-12 ? mag / Math.sqrt(e * this.preE) : 0;
    if (norm > this.maxNorm) this.maxNorm = norm;
    if (norm > this.threshold && (!this.run || norm > this.run.best)) this.run = { best: norm, at: idx, since: 0 };
    else if (this.run) {
      this.run.since++;
      if (this.run.since > this.L / 3) { this.pending = { end: this.run.at, peak: this.run.best }; this.run = null; }
    }
    return null;
  }

  private readBits(start: number, peak: number): Decoded | null {
    const bits: number[] = [];
    for (let k = 0; k < NBITS; k++) {
      const s = start + Math.round(k * this.Bf);
      const up = this.corr(this.tplUp, this.B, s).mag, dn = this.corr(this.tplDn, this.B, s).mag;
      bits.push(up > dn ? 1 : 0);
    }
    const f = bitsToFrame(bits);
    if (!f) return null;
    const p2 = Math.min(0.999, peak * peak);
    return { ...f, snrDb: 10 * Math.log10(p2 / (1 - p2)), atSample: this.sampleCount };
  }
}
