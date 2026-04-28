"""Referral system endpoints.

Each authenticated user owns a deterministic 8-char code (`SX` + base36 of
their user_id, salt-padded). The code is generated lazily on first
GET /referral/me call and persisted on the User row so signup-time
lookups are O(1) without a JOIN.

Referral attribution flow:
  1. New signup hits /auth/register with `?ref=SX1A2B3C` query param OR
     posts `referral_code` in the request body.
  2. /auth/register looks up the referrer by code, writes a Referral row
     (status=pending, earnings_usdt=0).
  3. When the referee converts to a paid subscription (handled by the
     payments webhook in app/api/routes_payments.py), the referral row
     transitions to status=qualified and 20% of the subscription amount
     is credited to earnings_usdt for the first 12 months.
  4. Payout is initiated by the referrer via a future
     /referral/payout endpoint (stub; runs through TON Wallet Pay).
"""
from __future__ import annotations

import string
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.models import Referral, User
from app.database.session import get_db

router = APIRouter()

# Base32 alphabet without confusing characters (no 0/O, 1/I/L) so referral
# codes are easy to read aloud or copy from a screenshot.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _encode(n: int) -> str:
    if n == 0:
        return _ALPHABET[0]
    out = []
    while n > 0:
        n, r = divmod(n, len(_ALPHABET))
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def code_for_user(user_id: int) -> str:
    """Deterministic referral code from user_id. Padded to ≥6 chars."""
    suffix = _encode(user_id).rjust(6, _ALPHABET[0])
    return f"SX{suffix}"


@router.get("/referral/me")
def my_referral(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the caller's referral code, count of referees, and earnings.

    Generates and persists the code on first call.
    """
    if not user.referral_code:
        user.referral_code = code_for_user(user.id)
        db.add(user)
        db.commit()
        db.refresh(user)

    referrals = (
        db.query(Referral).filter(Referral.referrer_id == user.id).all()
    )
    total_earnings = sum(r.earnings_usdt or 0.0 for r in referrals)
    qualified = sum(1 for r in referrals if r.status == "qualified")

    return {
        "code": user.referral_code,
        "referees_total": len(referrals),
        "referees_qualified": qualified,
        "earnings_usdt": round(total_earnings, 2),
        "share_url": f"https://signalx.app/?ref={user.referral_code}",
        "share_telegram": (
            f"https://t.me/share/url?url=https%3A%2F%2Fsignalx.app%2F%3Fref%3D{user.referral_code}"
            "&text=News-driven%20stock%20perp%20signals%20on%20Telegram%20%E2%80%94%20use%20my%20code"
        ),
    }


@router.get("/admin/referrals")
def admin_referrals(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 200,
) -> dict[str, Any]:
    """Aggregate referral stats for the admin dashboard.

    Admin or manager only. Returns a leaderboard of top referrers and
    the last `limit` referral rows.
    """
    if user.role not in ("admin", "manager"):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="manager+ required")

    leaderboard_rows = (
        db.query(
            Referral.referrer_id,
            func.count(Referral.id).label("count"),
            func.sum(Referral.earnings_usdt).label("earnings"),
        )
        .group_by(Referral.referrer_id)
        .order_by(func.sum(Referral.earnings_usdt).desc().nullslast())
        .limit(20)
        .all()
    )
    leaderboard = []
    for r in leaderboard_rows:
        u = db.query(User).filter(User.id == r.referrer_id).first()
        leaderboard.append({
            "referrer_id": r.referrer_id,
            "referrer_email": u.email if u else None,
            "code": (u.referral_code if u else None),
            "count": r.count,
            "earnings_usdt": round(r.earnings or 0.0, 2),
        })

    recent_rows = (
        db.query(Referral).order_by(Referral.created_at.desc()).limit(limit).all()
    )
    recent = [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "code": r.code,
            "referrer_id": r.referrer_id,
            "referee_id": r.referee_id,
            "status": r.status,
            "first_paid_at": r.first_paid_at.isoformat() if r.first_paid_at else None,
            "earnings_usdt": r.earnings_usdt,
        }
        for r in recent_rows
    ]

    last_30d = datetime.utcnow() - timedelta(days=30)
    return {
        "leaderboard": leaderboard,
        "recent": recent,
        "totals": {
            "referrals_all_time": db.query(Referral).count(),
            "qualified_30d": (
                db.query(Referral)
                .filter(Referral.status == "qualified")
                .filter(Referral.first_paid_at >= last_30d)
                .count()
            ),
        },
    }
