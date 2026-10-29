"""Reference-photo encryption at rest and short-lived signed photo URLs."""
import base64
import hashlib
import hmac
import time

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app import config
from app.models import AuditLog


def _fernet() -> Fernet:
    key = config.PHOTO_KEY or base64.urlsafe_b64encode(hashlib.sha256(b"photo|" + config.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key)


def encrypt(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt(blob: bytes) -> bytes:
    return _fernet().decrypt(blob)


def sniff_image(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _sig(student_id: str, prof_id: str, exp: int) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"photo|{student_id}|{prof_id}|{exp}".encode(), hashlib.sha256).hexdigest()


def photo_url(student_id: str, prof_id: str) -> str:
    exp = int(time.time()) + config.PHOTO_URL_TTL_S
    return f"/api/v1/students/{student_id}/photo?exp={exp}&sig={_sig(student_id, prof_id, exp)}"


def photo_sig_ok(student_id: str, prof_id: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(_sig(student_id, prof_id, exp), sig)


def audit(db: Session, actor_id: str | None, action: str, target: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target=target))
