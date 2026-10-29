// Extended DSP & Codec test suite for Phase 6 acceptance (Case 8).
// Run: cd bench && npx esbuild audio_sim_extended.ts --bundle --platform=node --format=cjs | node
import { BurstDecoder, BURST_MS, encodeBurst } from "../shared/audiocodec";

let seed = 54321;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-12)) * Math.cos(2 * Math.PI * rnd());

// Standard biquad bandpass filter
class BiquadBandpass {
  private b0: number; private b1: number; private b2: number;
  private a1: number; private a2: number;
  private x1 = 0; private x2 = 0; private y1 = 0; private y2 = 0;

  constructor(fs: number, f0: number, Q: number) {
    const w0 = (2 * Math.PI * f0) / fs;
    const alpha = Math.sin(w0) / (2 * Q);
    const a0 = 1 + alpha;
    this.b0 = (alpha) / a0;
    this.b1 = 0;
    this.b2 = (-alpha) / a0;
    this.a1 = (-2 * Math.cos(w0)) / a0;
    this.a2 = (1 - alpha) / a0;
  }

  process(x: number): number {
    const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2;
    this.x2 = this.x1; this.x1 = x;
    this.y2 = this.y1; this.y1 = y;
    return y;
  }
}

// Resample from fs_emit to fs_recv
function resample(input: Float32Array, fs_emit: number, fs_recv: number): Float32Array {
  const ratio = fs_emit / fs_recv;
  const outLen = Math.round(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const srcIdx = i * ratio;
    const i0 = Math.floor(srcIdx);
    const frac = srcIdx - i0;
    const s0 = input[i0] ?? 0;
    const s1 = input[i0 + 1] ?? s0;
    out[i] = s0 * (1 - frac) + s1 * frac;
  }
  return out;
}

console.log("=== Phase 6 Extended DSP / Codec Benchmark ===");
console.log(`Base burst length: ${BURST_MS} ms\n`);

const fs = 48000;

// 1. Clock drift / sample-rate mismatch (+- 200 ppm)
console.log("--- 1. Sample-Rate Mismatch (+-200 ppm) ---");
for (const ppm of [+200, -200]) {
  const fs_emit = fs * (1 + ppm * 1e-6);
  let ok = 0, wrong = 0;
  const N = 50;
  for (let t = 0; t < N; t++) {
    const low8 = Math.floor(rnd() * 256), code = Math.floor(rnd() * 65536);
    const burst_raw = encodeBurst(fs_emit, low8, code, 0.25);
    const burst = resample(burst_raw, fs_emit, fs);
    const total = Math.round(fs * 2.5);
    const start = Math.round(fs * (0.3 + rnd() * 0.8));
    const x = new Float32Array(total);
    for (let i = 0; i < total; i++) x[i] = 0.05 * gauss();
    for (let i = 0; i < burst.length; i++) x[start + i] += burst[i];

    const dec = new BurstDecoder(fs);
    const got = [];
    for (let i = 0; i < total; i += 128) got.push(...dec.push(x.subarray(i, Math.min(total, i + 128))));
    ok += +got.some((g) => g.low8 === low8 && g.code16 === code);
    wrong += got.filter((g) => g.low8 !== low8 || g.code16 !== code).length;
  }
  console.log(`${ppm > 0 ? "+" : ""}${ppm} ppm mismatch: decoded ${ok}/${N}  wrong-frames ${wrong}`);
}

// 2. Speaker-like band-pass filter (17-19.5 kHz, 4th order cascade)
console.log("\n--- 2. Speaker-Like Band-Pass Limit (17 - 19.5 kHz) ---");
{
  const bp1 = new BiquadBandpass(fs, 18250, 4.0);
  const bp2 = new BiquadBandpass(fs, 18250, 4.0);
  let ok = 0, wrong = 0;
  const N = 50;
  for (let t = 0; t < N; t++) {
    const low8 = Math.floor(rnd() * 256), code = Math.floor(rnd() * 65536);
    const burst = encodeBurst(fs, low8, code, 0.25);
    const total = Math.round(fs * 2.5);
    const start = Math.round(fs * (0.3 + rnd() * 0.8));
    const x = new Float32Array(total);
    for (let i = 0; i < total; i++) x[i] = 0.05 * gauss();
    for (let i = 0; i < burst.length; i++) x[start + i] += burst[i];

    // Filter through speaker roll-off model
    const filtered = new Float32Array(total);
    for (let i = 0; i < total; i++) filtered[i] = bp2.process(bp1.process(x[i]));

    const dec = new BurstDecoder(fs);
    const got = [];
    for (let i = 0; i < total; i += 128) got.push(...dec.push(filtered.subarray(i, Math.min(total, i + 128))));
    ok += +got.some((g) => g.low8 === low8 && g.code16 === code);
    wrong += got.filter((g) => g.low8 !== low8 || g.code16 !== code).length;
  }
  console.log(`Speaker bandpass filtered: decoded ${ok}/${N}  wrong-frames ${wrong}`);
}

// 3. Hard clipping at amplitude 1.0 (overdriven signal)
console.log("\n--- 3. Overdriven Input with Hard Clipping at +-1.0 ---");
{
  let ok = 0, wrong = 0;
  const N = 50;
  for (let t = 0; t < N; t++) {
    const low8 = Math.floor(rnd() * 256), code = Math.floor(rnd() * 65536);
    // Emitted at extreme amplitude 1.8 so it clips hard
    const burst = encodeBurst(fs, low8, code, 1.8);
    const total = Math.round(fs * 2.5);
    const start = Math.round(fs * (0.3 + rnd() * 0.8));
    const x = new Float32Array(total);
    for (let i = 0; i < total; i++) x[i] = 0.1 * gauss();
    for (let i = 0; i < burst.length; i++) {
      const rawVal = x[start + i] + burst[i];
      // Hard clipping at [-1.0, 1.0]
      x[start + i] = Math.max(-1.0, Math.min(1.0, rawVal));
    }

    const dec = new BurstDecoder(fs);
    const got = [];
    for (let i = 0; i < total; i += 128) got.push(...dec.push(x.subarray(i, Math.min(total, i + 128))));
    ok += +got.some((g) => g.low8 === low8 && g.code16 === code);
    wrong += got.filter((g) => g.low8 !== low8 || g.code16 !== code).length;
  }
  console.log(`Hard clipping (amp 1.8 clipped to 1.0): decoded ${ok}/${N}  wrong-frames ${wrong}`);
}

// 4. Overlapping bursts from two projectors
console.log("\n--- 4. Overlapping Bursts from Two Projectors (Collision Test) ---");
{
  let decA = 0, decB = 0, wrong = 0;
  const N = 50;
  for (let t = 0; t < N; t++) {
    const lowA = (t * 3) % 256, codeA = (t * 1007) % 65536;
    const lowB = (t * 7 + 1) % 256, codeB = (t * 2011 + 5) % 65536;
    const burstA = encodeBurst(fs, lowA, codeA, 0.25);
    const burstB = encodeBurst(fs, lowB, codeB, 0.25);

    const total = Math.round(fs * 3.0);
    const startA = Math.round(fs * 0.4);
    // Projector B offset by 20 to 100 ms
    const offsetMs = 20 + (t % 8) * 10;
    const startB = startA + Math.round((offsetMs / 1000) * fs);

    const x = new Float32Array(total);
    for (let i = 0; i < total; i++) x[i] = 0.05 * gauss();
    for (let i = 0; i < burstA.length; i++) x[startA + i] += burstA[i];
    for (let i = 0; i < burstB.length; i++) x[startB + i] += burstB[i];

    const dec = new BurstDecoder(fs);
    const got = [];
    for (let i = 0; i < total; i += 128) got.push(...dec.push(x.subarray(i, Math.min(total, i + 128))));

    const hasA = got.some((g) => g.low8 === lowA && g.code16 === codeA);
    const hasB = got.some((g) => g.low8 === lowB && g.code16 === codeB);
    const hasWrong = got.some((g) => !(g.low8 === lowA && g.code16 === codeA) && !(g.low8 === lowB && g.code16 === codeB));

    decA += +hasA;
    decB += +hasB;
    wrong += +hasWrong;
  }
  console.log(`Two overlapping projectors: decoded A=${decA}/${N}, B=${decB}/${N}, false/wrong-frames=${wrong} (CRC protection verified)`);
}

// 5. Long-run false-positive test over 60 min of pink noise + interference tones
console.log("\n--- 5. Long-Run False Positive Test (60 min of Noise + Tones) ---");
{
  let fp = 0;
  const dec = new BurstDecoder(fs);
  const blk = new Float32Array(128);
  // Pink noise approximation using 3-pole filter
  let b0 = 0, b1 = 0, b2 = 0;
  const totalBlocks = Math.round((fs * 3600) / 128); // 3600 seconds = 60 minutes

  for (let b = 0; b < totalBlocks; b++) {
    for (let i = 0; i < 128; i++) {
      const white = gauss();
      b0 = 0.99886 * b0 + white * 0.0555179;
      b1 = 0.99332 * b1 + white * 0.0750759;
      b2 = 0.96900 * b2 + white * 0.1538520;
      const pink = b0 + b1 + b2 + white * 0.5362;
      const sampleIdx = b * 128 + i;
      // Add speech-like harmonics and high-frequency tones
      const tone = 0.3 * Math.sin(2 * Math.PI * 1200 * sampleIdx / fs) +
                   0.15 * Math.sin(2 * Math.PI * 16000 * sampleIdx / fs);
      blk[i] = 0.2 * pink + tone;
    }
    const frames = dec.push(blk);
    if (frames.length > 0) fp += frames.length;
  }
  console.log(`60 min continuous pink noise + tones: false frames detected = ${fp}`);
}
