"""KYC endpoints.

  POST /kyc/start           — issue SDK token, mark profile submitted.
  GET  /kyc/status          — current user's status.
  POST /kyc/webhook         — provider callback (HMAC-validated).
  POST /admin/kyc/{user_id}/override — manual approve/reject.
  GET  /admin/kyc/queue     — review queue (compliance officer).

The router is provider-agnostic — auth + parsing happen in
app.kyc.providers.<name>.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_role
from app.database.models import AmlEvent, KycProfile, User
from app.database.session import get_db
from app.kyc.registry import get_provider
from app.kyc.service import admin_override, apply_verdict, start_verification

log = logging.getLogger(__name__)
router = APIRouter()


class KycStatusResponse(BaseModel):
    status: str
    provider: str
    applicant_id: str | None = None
    sanctions_hit: bool = False
    pep_hit: bool = False
    sof_required: bool = False
    document_country: str | None = None
    reject_reasons: list[str] = Field(default_factory=list)
    verdict_at: str | None = None


class AdminOverrideBody(BaseModel):
    new_status: str
    reason: str = Field(..., min_length=4, max_length=1000)


@router.post("/kyc/start")
def kyc_start(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return start_verification(db, user)


@router.get("/kyc/status", response_model=KycStatusResponse)
def kyc_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KycStatusResponse:
    profile = (
        db.query(KycProfile).filter(KycProfile.user_id == user.id).first()
    )
    if profile is None:
        return KycStatusResponse(status="unverified", provider=get_provider().name)
    reasons: list[str] = []
    if profile.reject_reasons:
        try:
            reasons = list(json.loads(profile.reject_reasons))
        except Exception:
            reasons = []
    return KycStatusResponse(
        status=profile.status,
        provider=profile.provider,
        applicant_id=profile.applicant_id,
        sanctions_hit=profile.sanctions_hit,
        pep_hit=profile.pep_hit,
        sof_required=profile.sof_required,
        document_country=profile.document_country,
        reject_reasons=reasons,
        verdict_at=profile.verdict_at.isoformat() if profile.verdict_at else None,
    )


@router.post("/kyc/webhook")
async def kyc_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Provider callback. The router doesn't know which provider —
    it asks the registered provider to verify + parse. The mock
    provider trusts any payload; sumsub validates HMAC."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_provider()
    try:
        payload = provider.verify_webhook(headers, raw)
        verdict = provider.parse_webhook(payload)
    except ValueError as exc:
        # Bad signature OR bad JSON — both 401 to avoid leaking which.
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    profile = apply_verdict(db, verdict)
    if profile is None:
        # Unknown applicant — accept-but-ignore so retries don't pile up.
        return {"ok": True, "applied": False}
    return {"ok": True, "applied": True, "status": profile.status}


@router.get("/admin/kyc/queue")
def admin_kyc_queue(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("manager", "admin")),
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Compliance officer review queue."""
    rows = (
        db.query(KycProfile)
        .filter(KycProfile.status.in_(["submitted", "pending_review"]))
        .order_by(KycProfile.updated_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for p in rows:
        u = db.query(User).filter(User.id == p.user_id).first()
        reasons: list[str] = []
        if p.reject_reasons:
            try:
                reasons = list(json.loads(p.reject_reasons))
            except Exception:
                pass
        out.append({
            "user_id": p.user_id,
            "email": u.email if u else None,
            "applicant_id": p.applicant_id,
            "status": p.status,
            "sanctions_hit": p.sanctions_hit,
            "pep_hit": p.pep_hit,
            "sof_required": p.sof_required,
            "country": p.document_country,
            "reject_reasons": reasons,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })
    return out


@router.get("/admin/kyc/events")
def admin_kyc_events(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("manager", "admin")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = (
        db.query(AmlEvent)
        .order_by(AmlEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat(),
            "user_id": e.user_id,
            "actor_id": e.actor_id,
            "kind": e.kind,
            "detail": e.detail,
        }
        for e in rows
    ]


@router.post("/admin/kyc/{user_id}/override")
def admin_kyc_override(
    user_id: int,
    body: AdminOverrideBody,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        profile = admin_override(db, user_id, body.new_status, actor, body.reason)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "user_id": profile.user_id,
        "status": profile.status,
        "verdict_at": profile.verdict_at.isoformat() if profile.verdict_at else None,
    }
