"""Top-of-funnel lead capture (Marketing/Growth).

Public endpoint — anonymous callers can subscribe their email to the
newsletter. No PII beyond what they type. Idempotent: posting the same
email twice returns the existing row instead of an error.

Distinct from /investors/lead (HNW pipeline) and /auth/register
(authenticated user account)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.database.models import LeadCapture
from app.database.session import get_db

router = APIRouter()


_REF_RE = re.compile(r"^SX[A-Z2-9]{6,12}$")


class LeadCaptureIn(BaseModel):
    email: EmailStr
    referral_code: str | None = Field(default=None, max_length=16)
    utm_source: str | None = Field(default=None, max_length=64)
    utm_medium: str | None = Field(default=None, max_length=64)
    utm_campaign: str | None = Field(default=None, max_length=64)


@router.post("/leads/subscribe")
def subscribe(
    payload: LeadCaptureIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    email = str(payload.email).lower().strip()
    existing = db.query(LeadCapture).filter(LeadCapture.email == email).first()
    if existing is not None:
        return {"id": existing.id, "email": existing.email, "duplicate": True}

    # Strip junk referral codes silently — never reject the lead capture
    # over a malformed ref param. Marketing > strict input validation here.
    ref = payload.referral_code
    if ref and not _REF_RE.match(ref):
        ref = None

    row = LeadCapture(
        email=email,
        referral_code=ref,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "email": row.email, "duplicate": False}


@router.post("/leads/unsubscribe")
def unsubscribe(
    payload: LeadCaptureIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    email = str(payload.email).lower().strip()
    row = db.query(LeadCapture).filter(LeadCapture.email == email).first()
    if row is not None:
        db.delete(row)
        db.commit()
    # Always return 200 to avoid leaking which emails are subscribed.
    return {"ok": True}
