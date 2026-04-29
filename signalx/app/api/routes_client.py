"""Client-facing CRM endpoints — only sees own data."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.autotrade.risk_guard import daily_pnl
from app.database.models import AutoTradeOrder, AutoTradeSubscription, User
from app.database.session import get_db

router = APIRouter()


@router.get("/client/me/subscriptions")
def my_subscriptions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    # Two-clause filter mirrors _own_or_admin in routes_autotrade.py:
    #   - rows owned by user_id (any email)
    #   - rows with NULL user_id whose email matches (anonymous-then-claimed)
    # We deliberately do NOT match by email when user_id is set on the row,
    # otherwise User A could grant User B read access just by typing B's
    # email at subscribe-time. (BUG_0002 in
    # pr-review-job-2c2ebe1fc6814cffacdc1da6619c82de.)
    rows = (
        db.query(AutoTradeSubscription)
        .filter(
            (AutoTradeSubscription.user_id == user.id)
            | (
                AutoTradeSubscription.user_id.is_(None)
                & (func.lower(AutoTradeSubscription.email) == (user.email or "").lower())
            )
        )
        .order_by(AutoTradeSubscription.created_at.desc())
        .all()
    )
    return [_serialize_sub(r) for r in rows]


@router.get("/client/me/subscriptions/{sub_id}")
def my_subscription_detail(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_sub(db, user, sub_id)
    orders = (
        db.query(AutoTradeOrder)
        .filter(AutoTradeOrder.subscription_id == sub.id)
        .order_by(AutoTradeOrder.created_at.desc())
        .limit(50)
        .all()
    )
    body = _serialize_sub(sub)
    body["pnl_today"] = round(daily_pnl(orders), 2)
    body["recent_orders"] = [
        {
            "id": o.id,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "mode": o.mode,
            "symbol": o.symbol,
            "side": o.side,
            "qty": o.qty,
            "entry_price": o.entry_price,
            "status": o.status,
            "rejected_reason": o.rejected_reason,
            "realized_pnl": o.realized_pnl,
        }
        for o in orders
    ]
    return body


def _own_sub(db: Session, user: User, sub_id: int) -> AutoTradeSubscription:
    sub = (
        db.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.id == sub_id)
        .first()
    )
    if sub is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    # Mirror _own_or_admin: email match is only a fallback for anonymous
    # subscriptions (user_id IS NULL), never an alternative to user_id
    # ownership. Otherwise an attacker could subscribe with a victim's
    # email and read their trade history.
    if sub.user_id is not None:
        if sub.user_id != user.id:
            raise HTTPException(status_code=403, detail="not your subscription")
    else:
        if (sub.email or "").lower() != (user.email or "").lower():
            raise HTTPException(status_code=403, detail="not your subscription")
    return sub


def _serialize_sub(r: AutoTradeSubscription) -> dict[str, Any]:
    return {
        "id": r.id,
        "tier": r.tier,
        "exchange_id": r.exchange_id,
        "status": r.status,
        "live_trading_enabled": r.live_trading_enabled,
        "paper_until": r.paper_until.isoformat() if r.paper_until else None,
        "last_paused_reason": r.last_paused_reason,
        "max_position_pct": r.max_position_pct,
        "daily_loss_limit_pct": r.daily_loss_limit_pct,
        "min_signal_score": r.min_signal_score,
        "max_fake_risk": r.max_fake_risk,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
