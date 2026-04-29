"""KYC service layer — orchestrates provider calls + DB updates.

Pure functions, no HTTP. The router (app.api.routes_kyc) and the
admin dashboard both go through this.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.models import AmlEvent, KycProfile, User
from app.kyc.providers.base import KycVerdict
from app.kyc.registry import get_provider

log = logging.getLogger(__name__)


def get_or_create_profile(db: Session, user: User) -> KycProfile:
    profile = (
        db.query(KycProfile)
        .filter(KycProfile.user_id == user.id)
        .first()
    )
    if profile:
        return profile
    profile = KycProfile(
        user_id=user.id,
        provider=get_provider().name,
        status="unverified",
    )
    db.add(profile)
    db.flush()
    return profile


def start_verification(db: Session, user: User) -> dict:
    """Get an SDK token and mark the profile 'submitted'."""
    provider = get_provider()
    profile = get_or_create_profile(db, user)

    result = provider.start_verification(user.id, user.email, level=profile.level)
    profile.applicant_id = result.applicant_id
    if profile.status in ("unverified", "rejected"):
        # Don't overwrite an in-flight 'submitted' or 'approved'.
        profile.status = "submitted"
    db.add(profile)

    db.add(AmlEvent(
        user_id=user.id,
        actor_id=user.id,
        kind="kyc_start",
        detail=json.dumps({
            "provider": provider.name,
            "applicant_id": result.applicant_id,
            "level": profile.level,
        }),
    ))
    db.commit()
    db.refresh(profile)
    return {
        "applicant_id": result.applicant_id,
        "sdk_token": result.sdk_token,
        "expires_at": result.expires_at,
        "status": profile.status,
        "provider": provider.name,
    }


def apply_verdict(db: Session, verdict: KycVerdict) -> KycProfile | None:
    """Update KycProfile + log AmlEvent. Idempotent on event_id."""
    profile = (
        db.query(KycProfile)
        .filter(KycProfile.applicant_id == verdict.applicant_id)
        .first()
    )
    if profile is None:
        log.warning(
            "KYC verdict for unknown applicant_id=%s; skipping",
            verdict.applicant_id,
        )
        return None

    # Idempotency — same event_id should not be applied twice.
    if verdict.raw_event_id and profile.last_event_id == verdict.raw_event_id:
        return profile

    profile.status = verdict.status
    profile.sanctions_hit = profile.sanctions_hit or verdict.sanctions_hit
    profile.pep_hit = profile.pep_hit or verdict.pep_hit
    profile.sof_required = profile.sof_required or verdict.sof_required
    if verdict.document_country:
        profile.document_country = verdict.document_country
    if verdict.reject_reasons:
        profile.reject_reasons = json.dumps(list(verdict.reject_reasons))
    if verdict.raw_event_id:
        profile.last_event_id = verdict.raw_event_id
    if verdict.status in ("approved", "rejected"):
        profile.verdict_at = datetime.utcnow()
    db.add(profile)

    db.add(AmlEvent(
        user_id=profile.user_id,
        actor_id=None,
        kind="kyc_verdict",
        detail=json.dumps({
            "status": verdict.status,
            "sanctions_hit": verdict.sanctions_hit,
            "pep_hit": verdict.pep_hit,
            "sof_required": verdict.sof_required,
            "reject_reasons": list(verdict.reject_reasons),
            "event_id": verdict.raw_event_id,
        }),
    ))
    if verdict.sanctions_hit:
        db.add(AmlEvent(
            user_id=profile.user_id,
            kind="sanctions_hit",
            detail=json.dumps({"applicant_id": verdict.applicant_id}),
        ))
    db.commit()
    db.refresh(profile)
    return profile


def admin_override(
    db: Session,
    user_id: int,
    new_status: str,
    actor: User,
    reason: str,
) -> KycProfile:
    """Compliance officer / admin manual override. Always logs."""
    if new_status not in ("approved", "rejected", "pending_review", "unverified"):
        raise ValueError(f"invalid override status: {new_status}")
    profile = (
        db.query(KycProfile)
        .filter(KycProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        raise LookupError(f"no KYC profile for user_id={user_id}")
    old_status = profile.status
    profile.status = new_status
    profile.verdict_at = datetime.utcnow()
    db.add(profile)
    db.add(AmlEvent(
        user_id=user_id,
        actor_id=actor.id,
        kind="override",
        detail=json.dumps({
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason[:1000],
        }),
    ))
    db.commit()
    db.refresh(profile)
    return profile
