// Offline loopback of the audio codec: noise, loud in-band-adjacent interference, echo, level, sample rates.
// Run: cd bench && npx esbuild audio_sim.ts --bundle --platform=node --format=cjs | node
import { BurstDecoder, BURST_MS, encodeBurst } from "../shared/audiocodec";

let seed = 12345;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-12)) * Math.cos(2 * Math.PI * rnd());

function trial(fs: number, sigma: number, opts: { echo?: boolean; tones?: boolean; amp?: number; low8?: number; code?: number }) {
  const low8 = opts.low8 ?? Math.floor(rnd() * 256), code = opts.code ?? Math.floor(rnd() * 65536);
  const burst = encodeBurst(fs, low8, code, opts.amp ?? 0.25);
  const total = Math.round(fs * 2.5), start = Math.round(fs * (0.3 + rnd() * 0.8));
  const x = new Float32Array(total);
  for (let i = 0; i < total; i++) x[i] = sigma * gauss();
  for (let i = 0; i < burst.length; i++) { x[start + i] += burst[i]; if (opts.echo) x[start + i + Math.round(0.02 * fs)] += 0.5 * burst[i]; }
  if (opts.tones) for (let i = 0; i < total; i++) x[i] += 0.5 * Math.sin(2 * Math.PI * 1000 * i / fs) + 0.3 * Math.sin(2 * Math.PI * 15000 * i / fs) + 0.15 * Math.sin(2 * Math.PI * 16500 * i / fs);
  const dec = new BurstDecoder(fs); const got = [];
  for (let i = 0; i < total; i += 128) got.push(...dec.push(x.subarray(i, Math.min(total, i + 128))));
  return { ok: got.some((g) => g.low8 === low8 && g.code16 === code), wrong: got.filter((g) => g.low8 !== low8 || g.code16 !== code).length, snr: got[0]?.snrDb };
}

console.log(`burst ${BURST_MS} ms`);
for (const fs of [48000, 44100]) {
  for (const [name, sigma, o] of [
    ["clean", 0.001, {}], ["noise 0.1 (amp .25: -8 dB broadband)", 0.1, {}], ["noise 0.25 (-14 dB)", 0.25, {}], ["noise 0.5 (-20 dB)", 0.5, {}],
    ["echo 20ms/0.5 + noise .1", 0.1, { echo: true }], ["loud tones 1k/15k/16.5k + noise .1", 0.1, { tones: true }], ["quiet amp .05 + noise .02", 0.02, { amp: 0.05 }],
  ] as [string, number, any][]) {
    let ok = 0, wrong = 0; const N = 100;
    for (let t = 0; t < N; t++) { const r = trial(fs, sigma, o); ok += +r.ok; wrong += r.wrong; }
    console.log(`${fs} Hz  ${name.padEnd(40)} decoded ${ok}/${N}  wrong-frames ${wrong}`);
  }
  // false positives: 10 minutes of pure noise + tones
  let fp = 0; const dec = new BurstDecoder(fs); const blk = new Float32Array(128);
  for (let b = 0; b < (fs * 600) / 128; b++) { for (let i = 0; i < 128; i++) blk[i] = 0.2 * gauss() + 0.5 * Math.sin((2 * Math.PI * 1000 * (b * 128 + i)) / fs); fp += dec.push(blk).length; }
  console.log(`${fs} Hz  10 min of noise+tone: false frames ${fp}`);
}
