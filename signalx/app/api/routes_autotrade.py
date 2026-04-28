"""Auto-trade subscription endpoints.

Onboarding flow:

  POST /autotrade/subscribe
    → creates a paper-mode subscription, encrypts the API keys, returns id.

  POST /autotrade/{id}/go-live
    → flips `live_trading_enabled=True`. Refused if the global kill switch
      (`Settings.enable_autotrade`) is False — operator must enable that
      first.

  POST /autotrade/{id}/kill        → status="killed" (immediate stop)
  POST /autotrade/{id}/paper-mode  → status="paper"  (back to paper)
  POST /autotrade/{id}/resume      → status="paper"  (clears 'paused')
  GET  /autotrade/{id}/status      → balance, pnl_today, recent orders
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_current_user_optional
from app.autotrade.crypto import encrypt
from app.autotrade.risk_guard import daily_pnl
from app.config.settings import get_settings
from app.database.models import AutoTradeOrder, AutoTradeSubscription, User
from app.database.session import get_db

router = APIRouter()

TIERS = ("manual_plus", "auto_lite", "auto_pro", "vip")


def _own_or_admin(db: Session, sub_id: int, user: User) -> AutoTradeSubscription:
    """Fetch subscription and assert the caller owns it (by user_id or email)
    or is an admin. Used by every mutation + status endpoint to prevent
    anonymous / cross-tenant access."""
    sub = _get_sub(db, sub_id)
    if user.role == "admin":
        return sub
    if sub.user_id is not None and sub.user_id == user.id:
        return sub
    if sub.user_id is None and (sub.email or "").lower() == (user.email or "").lower():
        return sub
    raise HTTPException(status_code=403, detail="not your subscription")


class SubscribeIn(BaseModel):
    email: EmailStr
    tier: Literal["manual_plus", "auto_lite", "auto_pro", "vip"]
    exchange_id: str = Field(..., examples=["bybit"])
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    api_passphrase: str | None = None
    max_position_pct: float | None = None
    daily_loss_limit_pct: float | None = None
    min_signal_score: int | None = None
    max_fake_risk: int | None = None
    allowed_symbols: list[str] | None = None


@router.post("/autotrade/subscribe")
def subscribe(
    payload: SubscribeIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    s = get_settings()
    sub = AutoTradeSubscription(
        email=str(payload.email),
        user_id=user.id if user else None,
        tier=payload.tier,
        exchange_id=payload.exchange_id.lower(),
        api_key_encrypted=encrypt(payload.api_key),
        api_secret_encrypted=encrypt(payload.api_secret),
        api_passphrase_encrypted=encrypt(payload.api_passphrase) if payload.api_passphrase else None,
        max_position_pct=payload.max_position_pct if payload.max_position_pct is not None else s.autotrade_default_max_position_pct,
        daily_loss_limit_pct=payload.daily_loss_limit_pct if payload.daily_loss_limit_pct is not None else s.autotrade_default_daily_loss_limit_pct,
        min_signal_score=payload.min_signal_score if payload.min_signal_score is not None else 60,
        max_fake_risk=payload.max_fake_risk if payload.max_fake_risk is not None else 49,
        allowed_symbols=json.dumps(payload.allowed_symbols) if payload.allowed_symbols else None,
        status="paper",
        live_trading_enabled=False,
        paper_until=datetime.utcnow() + timedelta(days=s.autotrade_default_paper_days),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {
        "subscription_id": sub.id,
        "tier": sub.tier,
        "status": sub.status,
        "paper_until": sub.paper_until.isoformat() if sub.paper_until else None,
        "live_trading_enabled": sub.live_trading_enabled,
    }


@router.post("/autotrade/{sub_id}/go-live")
def go_live(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    s = get_settings()
    sub = _own_or_admin(db, sub_id, user)
    if not s.enable_autotrade:
        raise HTTPException(
            status_code=409,
            detail="global autotrade kill switch is off (ENABLE_AUTOTRADE=false)",
        )
    if sub.paper_until and sub.paper_until > datetime.utcnow():
        raise HTTPException(
            status_code=409,
            detail=f"subscription is in paper mode until {sub.paper_until.isoformat()}",
        )
    sub.status = "live"
    sub.live_trading_enabled = True
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": sub.status, "live_trading_enabled": True}


@router.post("/autotrade/{sub_id}/kill")
def kill(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    sub.status = "killed"
    sub.live_trading_enabled = False
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "killed"}


@router.post("/autotrade/{sub_id}/paper-mode")
def paper_mode(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    sub.status = "paper"
    sub.live_trading_enabled = False
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "paper"}


@router.post("/autotrade/{sub_id}/resume")
def resume(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    if sub.status not in ("paused", "killed"):
        raise HTTPException(status_code=409, detail=f"subscription is {sub.status}, nothing to resume")
    sub.status = "paper"
    sub.live_trading_enabled = False
    sub.last_paused_reason = None
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "paper"}


@router.get("/autotrade/{sub_id}/status")
def status(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    orders = (
        db.query(AutoTradeOrder)
        .filter(AutoTradeOrder.subscription_id == sub.id)
        .order_by(AutoTradeOrder.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "subscription_id": sub.id,
        "tier": sub.tier,
        "status": sub.status,
        "live_trading_enabled": sub.live_trading_enabled,
        "paper_until": sub.paper_until.isoformat() if sub.paper_until else None,
        "last_paused_reason": sub.last_paused_reason,
        "exchange_id": sub.exchange_id,
        "max_position_pct": sub.max_position_pct,
        "daily_loss_limit_pct": sub.daily_loss_limit_pct,
        "min_signal_score": sub.min_signal_score,
        "max_fake_risk": sub.max_fake_risk,
        "pnl_today": round(daily_pnl(orders), 2),
        "recent_orders": [
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
        ],
    }


def _get_sub(db: Session, sub_id: int) -> AutoTradeSubscription:
    sub = db.query(AutoTradeSubscription).filter(AutoTradeSubscription.id == sub_id).first()
    if sub is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    return sub
