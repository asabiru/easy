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
    Payment,
    Signal,
    SignalResult,
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
    # impact_score and confidence live on the related NewsEvent row, not on
    # Signal — left-join so signals without an event still serialize cleanly.
    # (BUG_pr-review-job-f77e2282cf15490e8165842c71a28937_0001.)
    rows = (
        db.query(Signal, NewsEvent.impact_score, NewsEvent.confidence)
        .outerjoin(NewsEvent, Signal.event_id == NewsEvent.id)
        .order_by(Signal.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "ticker": r.ticker,
            "symbol": r.symbol,
            "action": r.action,
            "direction": r.direction,
            "impact_score": impact_score,
            "confidence": confidence,
            "signal_score": r.signal_score,
            "risk_level": r.risk_level,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r, impact_score, confidence in rows
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


# ─────────────────────────── metrics dashboards ─────────────────────────── #

# Subscription tier monthly price in USDT. Used by /admin/metrics/revenue
# to compute MRR. Source-of-truth pricing — must match landing-page
# pricing copy. When pricing changes, update both this map and
# `site/index.html` pricing section together.
TIER_PRICE_USDT = {
    "manual_plus": 99.0,
    "auto_lite": 249.0,
    "auto_pro": 499.0,
    "vip": 999.0,
}


@router.get("/admin/metrics/revenue")
def metrics_revenue(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),  # manager+ for visibility
) -> dict[str, Any]:
    """Revenue dashboard: MRR, ARR projection, churn 30d, active by tier.

    MRR is computed from the active subscription tier mix multiplied by
    the public price card (TIER_PRICE_USDT). ARR projection is a naive
    `MRR × 12` — stable for narrow comp ranges, undercount-biased for
    high-tier mix shifts (Auto-Pro / VIP have higher churn). Churn-30d
    is `cancellations_last_30d / active_subs_at_start_of_window`.
    """
    now = datetime.utcnow()
    window_start = now - timedelta(days=30)

    active_subs = (
        db.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.status.in_(("paper", "live")))
        .all()
    )
    by_tier: dict[str, int] = {}
    for s in active_subs:
        by_tier[s.tier] = by_tier.get(s.tier, 0) + 1

    mrr = sum(by_tier.get(t, 0) * p for t, p in TIER_PRICE_USDT.items())

    # Churn = subs that moved to killed within the last 30d.
    # The status_change audit-log entries don't capture this perfectly
    # (no enforced provenance), so we approximate via the killed-status
    # subscriptions whose updated_at is in-window. This will undercount
    # rapid-cycle reactivations, which is acceptable for an MVP-grade
    # cohort signal.
    killed_30d = (
        db.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.status == "killed")
        .filter(AutoTradeSubscription.updated_at >= window_start)
        .count()
    )
    active_count = len(active_subs)
    churn_pct = (
        round(100.0 * killed_30d / max(active_count + killed_30d, 1), 2)
    )

    # Cumulative paid revenue from /payments table (TON Wallet Pay).
    paid_payments = (
        db.query(Payment).filter(Payment.status == "paid").all()
    )
    revenue_lifetime = round(sum(p.amount_usdt for p in paid_payments), 2)
    revenue_30d = round(
        sum(
            p.amount_usdt
            for p in paid_payments
            if p.paid_at and p.paid_at >= window_start
        ),
        2,
    )

    return {
        "mrr_usdt": round(mrr, 2),
        "arr_projection_usdt": round(mrr * 12, 2),
        "active_subs": active_count,
        "active_by_tier": by_tier,
        "churn_30d_pct": churn_pct,
        "killed_30d": killed_30d,
        "revenue_paid_lifetime_usdt": revenue_lifetime,
        "revenue_paid_30d_usdt": revenue_30d,
        "tier_price_card": TIER_PRICE_USDT,
    }


@router.get("/admin/metrics/signal-quality")
def metrics_signal_quality(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
) -> dict[str, Any]:
    """Signal quality dashboard for the trading-risk skill.

    `win_rate_30d` only counts non-WATCH/SKIP signals — those don't
    have a directional thesis, so including them would dilute the
    metric. `avg_signal_score` is across all signals (WATCH and SKIP
    inclusive) to capture the engine's overall confidence trend.
    """
    window_start = datetime.utcnow() - timedelta(days=30)

    actionable = (
        db.query(Signal)
        .outerjoin(SignalResult, SignalResult.signal_id == Signal.id)
        .filter(Signal.created_at >= window_start)
        .filter(Signal.action.in_(("LONG", "SHORT")))
        .all()
    )
    wins = sum(1 for s in actionable if s.result and s.result.result == "win")
    losses = sum(1 for s in actionable if s.result and s.result.result == "loss")
    decided = wins + losses
    win_rate = round(100.0 * wins / decided, 2) if decided else None

    all_signals = (
        db.query(Signal).filter(Signal.created_at >= window_start).all()
    )
    by_action: dict[str, int] = {}
    for s in all_signals:
        by_action[s.action] = by_action.get(s.action, 0) + 1
    avg_score = (
        round(sum(s.signal_score for s in all_signals) / len(all_signals), 1)
        if all_signals else None
    )
    fake_risk_avg = round(
        sum(getattr(s.event, "fake_risk", 0) or 0 for s in all_signals)
        / max(len(all_signals), 1),
        1,
    ) if all_signals else None

    # Trading-Risk: alert when 7-day win rate dips below 50% and we have
    # at least 10 decided signals — small sample noise shouldn't trip
    # the alarm. This drives the orange banner on the admin dashboard
    # and (in a follow-up) emails Compliance via the agent ledger.
    seven_d_start = datetime.utcnow() - timedelta(days=7)
    last7 = [s for s in actionable if s.created_at and s.created_at >= seven_d_start]
    wins_7 = sum(1 for s in last7 if s.result and s.result.result == "win")
    losses_7 = sum(1 for s in last7 if s.result and s.result.result == "loss")
    decided_7 = wins_7 + losses_7
    win_rate_7d = round(100.0 * wins_7 / decided_7, 2) if decided_7 else None
    alert = (
        win_rate_7d is not None
        and win_rate_7d < 50.0
        and decided_7 >= 10
    )

    return {
        "window_days": 30,
        "signals_total": len(all_signals),
        "by_action": by_action,
        "actionable_count": len(actionable),
        "wins": wins,
        "losses": losses,
        "undecided": len(actionable) - decided,
        "win_rate_pct": win_rate,
        "win_rate_7d_pct": win_rate_7d,
        "win_rate_7d_decided": decided_7,
        "win_rate_alert": alert,
        "avg_signal_score": avg_score,
        "avg_fake_risk": fake_risk_avg,
    }


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


# ─────────────────────────── news poller ─────────────────────────── #

@router.post("/admin/news/poll-now")
def admin_poll_news_now(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Admin-triggered manual fan-out across every configured news source.

    Returns a per-source summary (fetched + emitted_signals + errors) so the
    operator can see which feeds are healthy. This is the same code path a
    Celery beat task would use — running it manually is the recommended way
    to validate a feeds.json change before scheduling it."""
    from app.news.poller import poll_all_sources

    summaries = poll_all_sources(db)
    _audit(
        db,
        actor=user,
        action="news.poll_now",
        target_type=None,
        target_id=None,
        payload={"sources": len(summaries), "errors": sum(1 for s in summaries if s.errors)},
    )
    db.commit()
    return {
        "summaries": [
            {
                "source_id": s.source_id,
                "kind": s.kind,
                "fetched": s.fetched,
                "emitted_signals": s.emitted_signals,
                "errors": s.errors or [],
            }
            for s in summaries
        ]
    }
