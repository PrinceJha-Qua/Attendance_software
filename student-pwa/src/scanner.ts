import wasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";

// Native BarcodeDetector where it exists (Chrome/Android: fast, hardware-assisted); zxing-wasm everywhere else (iOS Safari).
// zxing is a separate lazy chunk plus a self-hosted .wasm file, so it never counts against the app's first-load JS.
type ZX = typeof import("zxing-wasm/reader");
let zx: Promise<ZX> | null = null;

export function warmDecoder(): Promise<ZX> {
  zx ||= import("zxing-wasm/reader").then((m) => {
    m.setZXingModuleOverrides({ locateFile: (p, prefix) => (p.endsWith(".wasm") ? wasmUrl : prefix + p) });
    m.prepareZXingModule({ fireImmediately: true });
    return m;
  });
  return zx;
}

export type Decoder = { name: "native" | "zxing"; decode: (v: HTMLVideoElement) => Promise<string | null> };

export async function makeDecoder(): Promise<Decoder> {
  const BD = (window as any).BarcodeDetector;
  if (BD) {
    try {
      if ((await BD.getSupportedFormats()).includes("qr_code")) {
        const det = new BD({ formats: ["qr_code"] });
        return { name: "native", decode: async (v) => (await det.detect(v))[0]?.rawValue ?? null };
      }
    } catch { /* fall through to zxing */ }
  }
  const m = await warmDecoder();
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  return {
    name: "zxing",
    decode: async (v) => {
      canvas.width = v.videoWidth; canvas.height = v.videoHeight;
      ctx.drawImage(v, 0, 0);
      const res = await m.readBarcodes(ctx.getImageData(0, 0, canvas.width, canvas.height), { formats: ["QRCode"], maxNumberOfSymbols: 1, tryHarder: false, tryRotate: false, tryInvert: false });
      return res.find((r) => r.isValid)?.text ?? null;
    },
  };
}
