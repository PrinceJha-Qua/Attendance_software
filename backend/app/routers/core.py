"""Real in Phase 1: auth, /me, /time, health."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import ratelimit
from app.db import get_db
from app.deps import current_user
from app.models import User
from app.security import check_password, decode_token, hash_password, make_token
from app.storage import audit

router = APIRouter()

# Verifying against a throwaway hash keeps login time the same for unknown identifiers.
_DUMMY_HASH = hash_password("not-a-real-password")


class LoginIn(BaseModel):
    identifier: str  # email or roll number
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


def _tokens(user: User) -> dict:
    return {
        "access_token": make_token(user.id, user.role, "access"),
        "refresh_token": make_token(user.id, user.role, "refresh"),
        "role": user.role,
    }


@router.post("/auth/login", tags=["auth"])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ident = body.identifier.strip().lower()[:255]
    ip = request.client.host if request.client else "?"
    ratelimit.blocked("login", ident, 10)   # 10 failed attempts per identifier per 10 minutes; a success clears the count
    ratelimit.blocked("login-ip", ip, 200)  # generous: a campus NAT is one address
    user = db.scalar(select(User).where((User.email == body.identifier) | (User.roll_no == body.identifier)))
    ok = check_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        ratelimit.hit("login", ident, 10, 600)
        ratelimit.hit("login-ip", ip, 200, 600)
        audit(db, user.id if user else None, "auth.login_failed", ident[:128])
        db.commit()
        raise HTTPException(401, "INVALID_CREDENTIALS")
    ratelimit.reset("login", ident)
    audit(db, user.id, "auth.login")
    db.commit()
    return _tokens(user)


@router.post("/auth/refresh", tags=["auth"])
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    claims = decode_token(body.refresh_token, "refresh")
    user = db.get(User, claims["sub"]) if claims else None
    if not user:
        raise HTTPException(401, "INVALID_REFRESH_TOKEN")
    return _tokens(user)


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(_: User = Depends(current_user)):
    return None  # stateless JWT: the client discards tokens


@router.get("/me", tags=["auth"])
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "roll_no": user.roll_no, "role": user.role}


@router.get("/time", tags=["auth"])
def server_time(_: User = Depends(current_user)):
    return {"server_ms": int(time.time() * 1000)}


@router.get("/healthz", tags=["ops"], include_in_schema=True)
def healthz():
    return {"ok": True}


@router.get("/readyz", tags=["ops"])
def readyz(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"ok": True}
