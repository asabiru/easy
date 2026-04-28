"""Signals API."""
from __future__ import annotations

from typing import Any

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user_optional, require_role
from app.database.models import (
    AutoTradeSubscription,
    NewsEvent,
    Signal,
    SignalResult,
    User,
)
from app.database.session import get_db
from app.performance.metrics import summary

router = APIRouter()

# Per-tier fake-risk threshold. Lower = stricter. fake_risk is stored as
# an integer 0–100 (see app/analysis/fake_risk.py). Anonymous callers see
# only the cleanest signals; VIPs see everything. Calibrated against the
# Anti-Fake agent's noise floor: 30 = pristine, 70 = visibly fishy.
_FAKE_RISK_CAP_BY_TIER = {
    None: 30,
    "anonymous": 30,
    "manual_plus": 40,
    "auto_lite": 50,
    "auto_pro": 70,
    "vip": 100,
}


def _effective_tier(db: Session, user: User | None) -> str:
    """Resolve the highest tier the user is entitled to. Admin/manager
    bypass the gate (see all signals); regular clients get the cap of
    their best active subscription."""
    if user is None:
        return "anonymous"
    if user.role in ("admin", "manager"):
        return "vip"
    rank = {"manual_plus": 1, "auto_lite": 2, "auto_pro": 3, "vip": 4}
    best = "anonymous"
    best_rank = 0
    subs = (
        db.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.user_id == user.id)
        .filter(AutoTradeSubscription.status.in_(("paper", "live")))
        .all()
    )
    for s in subs:
        r = rank.get(s.tier, 0)
        if r > best_rank:
            best_rank = r
            best = s.tier
    return best


def _signal_to_dict(s: Signal) -> dict[str, Any]:
    # fake_risk lives on NewsEvent (the parent of Signal). Surface it on
    # the signal dict so the UI can show the noise score next to each
    # row without doing a second round-trip per signal.
    fake_risk = getattr(getattr(s, "event", None), "fake_risk", None)
    return {
        "id": s.id,
        "event_id": s.event_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "ticker": s.ticker,
        "symbol": s.symbol,
        "direction": s.direction,
        "action": s.action,
        "signal_score": s.signal_score,
        "fake_risk": fake_risk,
        "entry_price": s.entry_price,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "max_holding_minutes": s.max_holding_minutes,
        "risk_level": s.risk_level,
        "reason": s.reason,
        "status": s.status,
    }


@router.get("/signals")
def list_signals(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> list[dict[str, Any]]:
    """Return signals filtered by the caller's tier-based fake-risk cap.

    Anonymous callers see only the cleanest (fake_risk ≤ 30) signals.
    VIPs and staff see everything. The cap is calibrated by the
    Anti-Fake agent against historical noise; raising it above 0.50
    materially reduces win rate and is intentionally gated by tier.
    """
    tier = _effective_tier(db, user)
    cap = _FAKE_RISK_CAP_BY_TIER.get(tier, 30)
    # fake_risk is stored on NewsEvent — JOIN to filter without an N+1.
    rows = (
        db.query(Signal)
        .join(NewsEvent, Signal.event_id == NewsEvent.id)
        .filter(NewsEvent.fake_risk <= cap)
        .order_by(Signal.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [_signal_to_dict(s) for s in rows]


@router.get("/signals/filter-stats")
def filter_stats(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    window_hours: int = 24,
) -> dict[str, Any]:
    """Anti-Fake transparency endpoint.

    Returns total signals produced in the last `window_hours` and how
    many were filtered out by the caller's tier cap. Surfaces the value
    of the higher tiers without leaking the actual signals.
    """
    tier = _effective_tier(db, user)
    cap = _FAKE_RISK_CAP_BY_TIER.get(tier, 30)
    since = datetime.utcnow() - timedelta(hours=max(1, min(window_hours, 168)))
    base = (
        db.query(Signal)
        .join(NewsEvent, Signal.event_id == NewsEvent.id)
        .filter(Signal.created_at >= since)
    )
    total = base.count()
    visible_rows = base.filter(NewsEvent.fake_risk <= cap).all()
    visible = len(visible_rows)
    if visible:
        avg_score = sum(float(s.signal_score or 0) for s in visible_rows) / visible
        # Fake risk lives on the event — pull it via the relationship; the
        # join above guarantees the event is loaded (no extra round-trip).
        avg_risk = sum(float(s.event.fake_risk or 0) for s in visible_rows) / visible
    else:
        avg_score = 0.0
        avg_risk = 0.0
    return {
        "window_hours": window_hours,
        "tier": tier,
        "fake_risk_cap": cap,
        "signals_total": total,
        "signals_visible": visible,
        "signals_filtered": max(0, total - visible),
        "filtered_pct": round(100.0 * (total - visible) / max(total, 1), 1),
        "avg_visible_score": round(avg_score, 1),
        "avg_visible_fake_risk": round(avg_risk, 1),
    }


@router.get("/signals/{signal_id}")
def get_signal(signal_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Signal, signal_id)
    if s is None:
        raise HTTPException(404, "signal not found")
    out = _signal_to_dict(s)
    if s.result is not None:
        out["result"] = {
            "price_after_1m": s.result.price_after_1m,
            "price_after_3m": s.result.price_after_3m,
            "price_after_5m": s.result.price_after_5m,
            "price_after_15m": s.result.price_after_15m,
            "max_favorable_move": s.result.max_favorable_move,
            "max_adverse_move": s.result.max_adverse_move,
            "result": s.result.result,
            "notes": s.result.notes,
        }
    return out


class ResultUpdate(BaseModel):
    price_after_1m: float | None = None
    price_after_3m: float | None = None
    price_after_5m: float | None = None
    price_after_15m: float | None = None
    max_favorable_move: float | None = None
    max_adverse_move: float | None = None
    result: str | None = Field(None, pattern="^(win|loss|neutral|unknown)$")
    notes: str | None = None


@router.post("/signals/{signal_id}/result/update")
def update_result(
    signal_id: int,
    payload: ResultUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Admin-only. Signal results feed `win_rate_pct` on the public
    `/performance/summary`, which is referenced by the landing page —
    they cannot be writable by anonymous callers."""
    s = db.get(Signal, signal_id)
    if s is None:
        raise HTTPException(404, "signal not found")
    if s.result is None:
        s.result = SignalResult(signal_id=s.id)
        db.add(s.result)
        db.flush()
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(s.result, k, v)
    db.commit()
    db.refresh(s.result)
    return {"signal_id": s.id, "updated": True}


@router.get("/performance/summary")
def performance_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return summary(db)
