"""TOTP 2FA endpoints.

Flow:
  1. POST /auth/2fa/setup → returns provisioning URI (and base32 secret
     for manual entry). Server stores the secret on `User.totp_secret`
     but keeps `totp_enabled=False` until the user proves possession.
  2. POST /auth/2fa/verify {"code": "123456"} → flips totp_enabled=True
     on first success.
  3. POST /auth/2fa/disable {"code": "123456"} → only succeeds with a
     valid current code; clears the secret and flag.

Mandatory tiers: VIP and Auto-Pro must have `totp_enabled=True` before
/autotrade/{id}/go-live succeeds — see routes_autotrade.py:go_live.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.models import User
from app.database.session import get_db
from app.security.totp import new_secret, provisioning_uri, verify

router = APIRouter()


class CodeIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)


@router.post("/auth/2fa/setup")
def setup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a fresh provisioning URI + secret for QR-code rendering.

    Idempotent before enrolment: re-issues a new secret each call so
    users who started enrolment on a different device aren't stuck.
    Refuses (409) if 2FA is already enabled — caller must /disable first.
    """
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="2FA already enabled — disable first to rotate")
    secret = new_secret()
    user.totp_secret = secret
    db.add(user)
    db.commit()
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri(email=user.email, secret=secret),
    }


@router.post("/auth/2fa/verify")
def verify_code(
    payload: CodeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if not user.totp_secret:
        raise HTTPException(status_code=409, detail="no pending 2FA enrolment — POST /auth/2fa/setup first")
    if not verify(user.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="invalid TOTP code")
    user.totp_enabled = True
    db.add(user)
    db.commit()
    return {"totp_enabled": True}


@router.post("/auth/2fa/disable")
def disable(
    payload: CodeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Disable 2FA. Requires a valid current code to prevent a stolen
    session token from silently turning off 2FA on the victim's account."""
    if not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status_code=409, detail="2FA is not enabled")
    if not verify(user.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="invalid TOTP code")
    user.totp_secret = None
    user.totp_enabled = False
    db.add(user)
    db.commit()
    return {"totp_enabled": False}


@router.get("/auth/2fa/status")
def status(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"totp_enabled": bool(user.totp_enabled)}
