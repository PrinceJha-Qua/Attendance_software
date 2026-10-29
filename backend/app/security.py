import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app import config

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerificationError:
        return False


def make_token(user_id: str, role: str, kind: str) -> str:
    ttl = config.ACCESS_TTL_S if kind == "access" else config.REFRESH_TTL_S
    now = int(time.time())
    return jwt.encode({"sub": user_id, "role": role, "typ": kind, "iat": now, "exp": now + ttl}, config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str, kind: str) -> dict | None:
    try:
        claims = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return claims if claims.get("typ") == kind else None
