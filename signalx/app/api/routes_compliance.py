"""Public compliance endpoints (risk acknowledgement gate).

`/compliance/risk-ack` is the only state-mutating endpoint here. Audit
fields are appended to the user row directly (risk_ack_version + at)
for cheap lookups in the gate; the AML audit log already covers KYC
events separately."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.compliance.risk_ack import RISK_ACK_VERSION
from app.database.models import User
from app.database.session import get_db

router = APIRouter()


class RiskAckIn(BaseModel):
    version: int
    accepted: bool = True


@router.get("/compliance/risk-ack")
def get_status(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return whether the caller has accepted the *current* version."""
    accepted = (user.risk_ack_version or 0) >= RISK_ACK_VERSION
    return {
        "current_version": RISK_ACK_VERSION,
        "accepted_version": user.risk_ack_version or 0,
        "accepted": accepted,
        "accepted_at": user.risk_ack_at.isoformat() if user.risk_ack_at else None,
        "disclosures_url": "/legal/disclosures.html",
    }


@router.post("/compliance/risk-ack")
def accept(
    payload: RiskAckIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Record the caller's acceptance of the current risk-disclosure
    version. Refuses out-of-date payloads (version != current) so the
    UI can't accidentally pin acceptance to a stale disclosure."""
    if payload.version != RISK_ACK_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"version mismatch: server is on {RISK_ACK_VERSION}, payload sent {payload.version}",
        )
    if not payload.accepted:
        raise HTTPException(status_code=400, detail="acceptance must be explicit")
    user.risk_ack_version = RISK_ACK_VERSION
    user.risk_ack_at = datetime.utcnow()
    db.add(user)
    db.commit()
    return {
        "accepted_version": user.risk_ack_version,
        "accepted_at": user.risk_ack_at.isoformat(),
    }
