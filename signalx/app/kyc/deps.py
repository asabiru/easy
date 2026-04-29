"""FastAPI dependency: require_kyc().

Use as `Depends(require_kyc())` on every endpoint that:
  * unlocks live trading (`/autotrade/{id}/go-live`)
  * accepts client funds (any future `/payments/*/deposit`)
  * mutates the on-chain vault state (when added)

Behavior:
  * If `settings.kyc_required` is False, this is a no-op (used during
    early MVP / mock provider phase).
  * If True, looks up the caller's KycProfile; raises 403 unless
    `status == "approved"`.

Admin role bypasses the check (admin actions go through the
compliance dashboard which has its own audit-log trail).
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config.settings import get_settings
from app.database.models import KycProfile, User
from app.database.session import get_db

log = logging.getLogger(__name__)


def require_kyc(*, enforce: bool = False):
    """Dependency factory. Returns a function that asserts caller has
    `kyc_status == 'approved'` (or is admin).

    Args:
        enforce: When True, the check runs regardless of the
            `kyc_required` setting. Use this on endpoints that must
            never accept un-verified users — e.g. payment / deposit
            endpoints, where global KYC gating is non-negotiable
            (regulatory requirement). Defaults to False so the gate
            can be relaxed globally during the early MVP / mock
            provider phase.
    """

    def _checker(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not enforce and not get_settings().kyc_required:
            return user
        if user.role == "admin":
            return user
        profile = (
            db.query(KycProfile)
            .filter(KycProfile.user_id == user.id)
            .first()
        )
        if profile is None or profile.status != "approved":
            raise HTTPException(
                status_code=403,
                detail="KYC verification required. POST /kyc/start to begin.",
            )
        if profile.sanctions_hit:
            # Hard block — can never be overridden via the user-facing flow.
            # Compliance officer must manually clear via /admin/kyc/{id}/override.
            raise HTTPException(
                status_code=403, detail="account blocked by AML screening"
            )
        return user

    return _checker
