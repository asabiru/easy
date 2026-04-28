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
    # Use func.lower on both sides so the lookup is case-insensitive in
    # Postgres (= is case-sensitive there). Defense in depth on top of
    # the storage-side normalization in routes_autotrade.subscribe.
    rows = (
        db.query(AutoTradeSubscription)
        .filter(
            (AutoTradeSubscription.user_id == user.id)
            | (func.lower(AutoTradeSubscription.email) == (user.email or "").lower())
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
    if sub.user_id != user.id and (sub.email or "").lower() != (user.email or "").lower():
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
