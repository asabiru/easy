"""Admin endpoints — full visibility + force-actions.

Every action that changes state is recorded in `audit_log`."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.config.settings import get_settings
from app.database.models import (
    AuditLog,
    AutoTradeOrder,
    AutoTradeSubscription,
    InvestorLead,
    NewsEvent,
    Signal,
    SupportTicket,
    User,
)
from app.database.session import get_db

router = APIRouter()


def _audit(db: Session, *, actor: User, action: str, target_type: str | None = None, target_id: int | None = None, payload: Any = None) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=json.dumps(payload) if payload is not None else None,
        )
    )


# ─────────────────────────── system overview ─────────────────────────── #

@router.get("/admin/system")
def system_overview(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    s = get_settings()
    last_24h = datetime.utcnow() - timedelta(hours=24)
    return {
        "global_autotrade_enabled": s.enable_autotrade,
        "telegram_enabled": s.telegram_enabled,
        "users_count": db.query(User).count(),
        "subs_total": db.query(AutoTradeSubscription).count(),
        "subs_live": db.query(AutoTradeSubscription).filter(AutoTradeSubscription.status == "live").count(),
        "subs_paper": db.query(AutoTradeSubscription).filter(AutoTradeSubscription.status == "paper").count(),
        "subs_paused": db.query(AutoTradeSubscription).filter(AutoTradeSubscription.status == "paused").count(),
        "subs_killed": db.query(AutoTradeSubscription).filter(AutoTradeSubscription.status == "killed").count(),
        "signals_24h": db.query(Signal).filter(Signal.created_at >= last_24h).count(),
        "news_events_24h": db.query(NewsEvent).filter(NewsEvent.created_at >= last_24h).count(),
        "open_tickets": db.query(SupportTicket).filter(SupportTicket.status == "open").count(),
        "investor_leads_open": db.query(InvestorLead).filter(InvestorLead.status.in_(("new", "contacted"))).count(),
    }


# ─────────────────────────── users ─────────────────────────── #

@router.get("/admin/users")
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = db.query(User).order_by(User.created_at.desc()).limit(limit).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "role": u.role,
            "full_name": u.full_name,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in rows
    ]


class RoleChange(BaseModel):
    role: Literal["client", "manager", "admin"]


@router.post("/admin/users/{user_id}/role")
def set_user_role(
    user_id: int,
    payload: RoleChange,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    old = target.role
    target.role = payload.role
    db.add(target)
    _audit(db, actor=actor, action="user.set_role", target_type="user", target_id=user_id, payload={"from": old, "to": payload.role})
    db.commit()
    return {"user_id": target.id, "role": target.role}


@router.post("/admin/users/{user_id}/disable")
def disable_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    if user_id == actor.id:
        raise HTTPException(status_code=409, detail="cannot disable yourself")
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    target.is_active = False
    db.add(target)
    _audit(db, actor=actor, action="user.disable", target_type="user", target_id=user_id)
    db.commit()
    return {"user_id": target.id, "is_active": False}


# ─────────────────────────── subscriptions ─────────────────────────── #

@router.get("/admin/subscriptions")
def list_subscriptions(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = (
        db.query(AutoTradeSubscription)
        .order_by(AutoTradeSubscription.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "email": r.email,
            "user_id": r.user_id,
            "tier": r.tier,
            "exchange_id": r.exchange_id,
            "status": r.status,
            "live_trading_enabled": r.live_trading_enabled,
            "paper_until": r.paper_until.isoformat() if r.paper_until else None,
            "max_position_pct": r.max_position_pct,
            "daily_loss_limit_pct": r.daily_loss_limit_pct,
            "last_paused_reason": r.last_paused_reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/admin/subscriptions/{sub_id}/force-kill")
def force_kill(
    sub_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    sub = db.query(AutoTradeSubscription).filter(AutoTradeSubscription.id == sub_id).first()
    if sub is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    sub.status = "killed"
    sub.live_trading_enabled = False
    db.add(sub)
    _audit(db, actor=actor, action="autotrade.force_kill", target_type="autotrade_subscription", target_id=sub_id)
    db.commit()
    return {"subscription_id": sub.id, "status": "killed"}


# ─────────────────────────── signals ─────────────────────────── #

@router.get("/admin/signals")
def admin_signals(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = db.query(Signal).order_by(Signal.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "ticker": r.ticker,
            "symbol": r.symbol,
            "action": r.action,
            "direction": r.direction,
            "impact_score": r.impact_score,
            "confidence": r.confidence,
            "signal_score": r.signal_score,
            "risk_level": r.risk_level,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/admin/orders")
def admin_orders(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = db.query(AutoTradeOrder).order_by(AutoTradeOrder.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "subscription_id": r.subscription_id,
            "signal_id": r.signal_id,
            "mode": r.mode,
            "symbol": r.symbol,
            "side": r.side,
            "qty": r.qty,
            "entry_price": r.entry_price,
            "status": r.status,
            "rejected_reason": r.rejected_reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ─────────────────────────── audit ─────────────────────────── #

@router.get("/admin/audit")
def admin_audit(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "actor_user_id": r.actor_user_id,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "payload": r.payload,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
