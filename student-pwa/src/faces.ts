// On-device face embedding (one fixed model) and the colour-flash liveness sampler. Only numbers leave the phone.
// face-api (tfjs) is a lazy chunk; the ~6 MB of weights are self-hosted under /models and cached by the browser.
type FA = typeof import("@vladmandic/face-api");
let lib: Promise<FA> | null = null;

export function warmFaces(): Promise<FA> {
  lib ||= import("@vladmandic/face-api").then(async (fa) => {
    await Promise.all([fa.nets.tinyFaceDetector.loadFromUri("/models"), fa.nets.faceLandmark68TinyNet.loadFromUri("/models"), fa.nets.faceRecognitionNet.loadFromUri("/models")]);
    return fa;
  });
  lib.catch(() => { lib = null; });  // allow a retry after a failed download
  return lib;
}

const opts = (fa: FA) => new fa.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 });

export async function embed(el: HTMLVideoElement | HTMLImageElement | HTMLCanvasElement): Promise<{ vector: number[]; box: { x: number; y: number; width: number; height: number } } | null> {
  const fa = await warmFaces();
  const r = await fa.detectSingleFace(el, opts(fa)).withFaceLandmarks(true).withFaceDescriptor();
  return r ? { vector: Array.from(r.descriptor), box: r.detection.box } : null;
}

const cv = document.createElement("canvas");
const cx = cv.getContext("2d", { willReadFrequently: true })!;
/** Mean RGB of the middle of the face box (cheeks/forehead), where the screen's colour shows. */
function faceRgb(v: HTMLVideoElement, b: { x: number; y: number; width: number; height: number }): number[] {
  const w = Math.max(8, Math.round(b.width * 0.5)), h = Math.max(8, Math.round(b.height * 0.5));
  const x = Math.max(0, Math.round(b.x + b.width * 0.25)), y = Math.max(0, Math.round(b.y + b.height * 0.25));
  cv.width = w; cv.height = h; cx.drawImage(v, x, y, w, h, 0, 0, w, h);
  const d = cx.getImageData(0, 0, w, h).data; const s = [0, 0, 0]; const n = d.length / 4;
  for (let i = 0; i < d.length; i += 4) { s[0] += d[i]; s[1] += d[i + 1]; s[2] += d[i + 2]; }
  return s.map((t) => t / n);
}

export const COLOUR_CSS: Record<string, string> = { red: "#ff0000", green: "#00ff00", blue: "#0000ff", yellow: "#ffff00", magenta: "#ff00ff", cyan: "#00ffff" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const mean = (rows: number[][]) => [0, 1, 2].map((k) => rows.reduce((a, r) => a + r[k], 0) / rows.length);

export type FaceEvidence = { vector: number[]; liveness: { baseline: number[]; samples: number[][] } };

/** Neutral baseline (also yields the descriptor), then each challenge colour for ~450 ms (>= 2 changes/s is below the 3 flashes/s photosensitivity limit). */
export async function runFaceStep(v: HTMLVideoElement, colours: string[], setScreen: (css: string) => void, aborted: () => boolean): Promise<FaceEvidence | null> {
  const fa = await warmFaces();
  setScreen("#9a9a9a");
  let found: Awaited<ReturnType<typeof embed>> = null;
  for (let i = 0; i < 25 && !found && !aborted(); i++) { found = await embed(v); if (!found) await sleep(150); }
  if (!found) return null;
  const base: number[][] = [];
  for (let i = 0; i < 3; i++) { base.push(faceRgb(v, found.box)); await sleep(60); }
  const samples: number[][] = [];
  for (const c of colours) {
    setScreen(COLOUR_CSS[c] ?? "#fff"); await sleep(200);
    const got: number[][] = []; let box = found.box;
    for (let i = 0; i < 3; i++) {
      const d = await fa.detectSingleFace(v, opts(fa));
      if (d) box = d.box;
      got.push(faceRgb(v, box)); await sleep(60);
    }
    samples.push(mean(got));
  }
  setScreen("");
  return { vector: found.vector, liveness: { baseline: mean(base), samples } };
}
