"""Student side of enrollment and privacy (phase 2)."""
import json
import math

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.deps import require_role
from app.models import Consent, FaceTemplate, User
from app.storage import audit, encrypt, sniff_image

router = APIRouter(prefix="/students/me", tags=["student-enrollment"])
student_only = require_role("student")


class ConsentIn(BaseModel):
    text_version: str = Field(min_length=1, max_length=32)


@router.post("/consent", status_code=204)
def give_consent(body: ConsentIn, user: User = Depends(student_only), db: Session = Depends(get_db)):
    db.add(Consent(student_id=user.id, text_version=body.text_version))
    audit(db, user.id, "consent.given", body.text_version)
    db.commit()


def _parse_vector(raw: str) -> list[float]:
    try:
        vec = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "BAD_FACE_VECTOR")
    if not (isinstance(vec, list) and len(vec) == 128 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vec)):
        raise HTTPException(422, "BAD_FACE_VECTOR")
    return [float(x) for x in vec]


@router.post("/enroll", status_code=202)
async def enroll(
    photo: UploadFile = File(...),
    vector: str = Form(...),
    model: str = Form(..., min_length=1, max_length=32),
    user: User = Depends(student_only),
    db: Session = Depends(get_db),
):
    if model in config.REJECTED_MODELS:
        raise HTTPException(422, "BAD_MODEL")  # placeholder embeddings must never become templates
    if not db.scalar(select(Consent).where(Consent.student_id == user.id)):
        raise HTTPException(409, "CONSENT_REQUIRED")
    existing = db.get(FaceTemplate, user.id)
    if existing and existing.status in ("pending", "confirmed"):
        raise HTTPException(409, "ALREADY_ENROLLED")
    vec = _parse_vector(vector)
    data = await photo.read(config.MAX_PHOTO_BYTES + 1)
    if len(data) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, "PHOTO_TOO_LARGE")
    if not sniff_image(data):
        raise HTTPException(422, "BAD_PHOTO")
    if existing:  # rejected earlier: replace
        db.delete(existing)
        db.flush()
    db.add(FaceTemplate(student_id=user.id, vector=vec, model=model, photo_enc=encrypt(data), status="pending"))
    audit(db, user.id, "enroll.submitted")
    db.commit()
    return {"status": "pending"}


@router.get("/enrollment")
def enrollment_status(user: User = Depends(student_only), db: Session = Depends(get_db)):
    consent = db.scalar(select(Consent).where(Consent.student_id == user.id))
    tpl = db.get(FaceTemplate, user.id)
    return {
        "consent_given": consent is not None,
        "status": tpl.status if tpl else "none",
        "model": tpl.model if tpl else None,
        "confirmed_at": tpl.confirmed_at.isoformat() if tpl and tpl.confirmed_at else None,
    }


@router.delete("/face-data", status_code=204)
def delete_face_data(user: User = Depends(student_only), db: Session = Depends(get_db)):
    tpl = db.get(FaceTemplate, user.id)
    if tpl:
        db.delete(tpl)
    audit(db, user.id, "face_data.deleted")
    db.commit()
