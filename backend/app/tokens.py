"""Rolling QR token service. Spec: shared/TOKEN_SPEC.md

The client never holds a key: it only relays a scanned token, so there is nothing in
the browser to forge or extract. All verification happens here.
"""
import base64
import hashlib
import hmac
from dataclasses import dataclass

PREFIX = "gk1"
TOKEN_LEN = 16


def window_for(now_ms: int, step_ms: int) -> int:
    return now_ms // step_ms


def make_token(secret: bytes, session_id: str, window: int) -> str:
    msg = f"{session_id}|{window}".encode()
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    return base64.b32encode(digest).decode().rstrip("=")[:TOKEN_LEN]


def make_qr_payload(secret: bytes, session_id: str, window: int) -> str:
    return f"{PREFIX}:{session_id}:{window}:{make_token(secret, session_id, window)}"


@dataclass(frozen=True)
class Parsed:
    session_id: str
    window: int
    token: str


def parse_qr(payload: str) -> Parsed | None:
    parts = payload.split(":")
    if len(parts) != 4 or parts[0] != PREFIX or not parts[2].isdigit():
        return None
    return Parsed(parts[1], int(parts[2]), parts[3])


def verify(secret: bytes, parsed: Parsed, now_ms: int, step_ms: int, grace_windows: int = 1) -> str:
    """Returns 'ok', 'invalid' (forged/wrong session) or 'expired' (valid but outside the window)."""
    expected = make_token(secret, parsed.session_id, parsed.window)
    if not hmac.compare_digest(expected, parsed.token):
        return "invalid"
    now_w = window_for(now_ms, step_ms)
    if now_w - grace_windows <= parsed.window <= now_w:
        return "ok"
    return "expired"


def audio_code(secret: bytes, session_id: str, window: int) -> int:
    """16-bit code the projector emits as ultrasound for this window. Separate from the QR token, so a relayed QR image does not reveal it."""
    digest = hmac.new(secret, f"audio|{session_id}|{window}".encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:2], "big")
