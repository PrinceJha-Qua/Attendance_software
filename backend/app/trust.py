"""Pure trust scoring (no DB, no I/O). Hard gates (QR, roster) are enforced by the router before this runs."""
import math

from app import config

RGB = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0), "magenta": (255, 0, 255), "cyan": (0, 255, 255)}
REVIEW_FLAGS = {"FACE_MISMATCH", "FACE_COLLISION", "LIVENESS_FAIL", "FACE_MISSING", "LIVENESS_MISSING", "AUDIO_MISMATCH"}


def distance(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def _chroma(v) -> list[float]:
    m = sum(v) / 3
    return [x - m for x in v]


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.hypot(*a), math.hypot(*b)
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na > 1e-6 and nb > 1e-6 else 0.0


def liveness(colour_seq: list[str], baseline: list[float], samples: list[list[float]]) -> tuple[bool, list[float]]:
    """The phone lit the face with these colours; the face colour should shift toward each one.
    Compares the chroma of (sample - baseline) with the chroma of the screen colour. Returns (passed, per-step cosines)."""
    if len(samples) != len(colour_seq):
        return False, []
    cos = [_cos(_chroma([s - b for s, b in zip(sample, baseline)]), _chroma(RGB[c])) for sample, c in zip(samples, colour_seq)]
    return sum(c >= config.LIVENESS_MIN_COS for c in cos) >= config.LIVENESS_MIN_PASS, cos


def score(*, face_dist: float | None, live_ok: bool | None, collision: bool, audio: str | None = None) -> tuple[str, float, list[str]]:
    """face_dist None = no face evidence; live_ok None = no liveness evidence; audio None = session has no audio,
    else "ok" | "unavailable" (not heard: penalty, never a review flag) | "mismatch" (a valid burst of the wrong/stale window: review).
    Returns (status, score, flags)."""
    flags: list[str] = []
    total = config.W_QR
    if face_dist is None:
        flags.append("FACE_MISSING")
    elif face_dist <= config.FACE_DIST_MAX:
        total += config.W_FACE
    else:
        flags.append("FACE_MISMATCH")
    if live_ok is None:
        flags.append("LIVENESS_MISSING")
    elif live_ok:
        total += config.W_LIVENESS
    else:
        flags.append("LIVENESS_FAIL")
    if collision:
        flags.append("FACE_COLLISION")
    if audio == "unavailable":
        flags.append("AUDIO_UNAVAILABLE"); total -= config.AUDIO_MISSING_PENALTY
    elif audio == "mismatch":
        flags.append("AUDIO_MISMATCH"); total -= config.AUDIO_MISSING_PENALTY
    total = round(max(0.0, min(total, 1.0)), 3)
    review = bool(REVIEW_FLAGS & set(flags)) or total < config.VERIFIED_MIN
    return ("REVIEW" if review else "VERIFIED"), total, flags
