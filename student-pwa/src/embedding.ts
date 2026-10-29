import { embed } from "./faces";

// Must equal the backend's config.FACE_MODEL. The server rejects "stub-v0" and asks old templates to re-enroll.
export const EMBEDDING_MODEL = "faceapi-128-v1";

/** Descriptor of the reference selfie. Throws NO_FACE so the student retakes instead of enrolling a blank. */
export async function computeEmbedding(photo: Blob): Promise<number[]> {
  const img = new Image(); const url = URL.createObjectURL(photo);
  try {
    img.src = url; await img.decode();
    const r = await embed(img);
    if (!r) throw new Error("NO_FACE");
    return r.vector;
  } finally { URL.revokeObjectURL(url); }
}
