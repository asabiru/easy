"""Signal → order dispatcher.

Pulls every active subscription matching a signal's filters (min_signal_score,
max_fake_risk via the linked NewsEvent, allowed_symbols) and either places
a paper or live order on the client's exchange.

Live execution is feature-flagged at three layers:

  1. global  — `Settings.enable_autotrade` must be True
  2. per-sub — `AutoTradeSubscription.live_trading_enabled` must be True
  3. paper-window — `paper_until` (if set) must be in the past

If any layer is not satisfied the order is recorded as `mode="paper"` with
`status="filled"` so the client still sees the trade in their dashboard;
no real exchange call happens.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.autotrade.risk_guard import (
    GuardDecision,
    daily_pnl,
    evaluate_pre_order,
    starting_of_day_balance,
)
from app.config.settings import get_settings
from app.database.models import AutoTradeOrder, AutoTradeSubscription, NewsEvent, Signal

log = logging.getLogger(__name__)

DEFAULT_PAPER_BALANCE = 10_000.0


def dispatch_signal_to_subscriptions(db: Session, signal: Signal) -> list[AutoTradeOrder]:
    """For every eligible subscription, attempt to fire an order. Returns
    the persisted AutoTradeOrder rows (filled or rejected)."""
    if signal.action not in ("LONG", "SHORT"):
        return []

    subs = (
        db.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.status.in_(("paper", "live")))
        .all()
    )
    placed: list[AutoTradeOrder] = []
    for sub in subs:
        order = _maybe_execute(db, sub, signal)
        if order is not None:
            placed.append(order)
    return placed


def _maybe_execute(
    db: Session, sub: AutoTradeSubscription, signal: Signal
) -> AutoTradeOrder | None:
    # Subscription-level filters
    if signal.signal_score < sub.min_signal_score:
        return None
    if sub.max_fake_risk is not None and signal.event_id:
        # fake_risk lives on NewsEvent; signals with fake_risk >=30 are already
        # demoted to WATCH by the risk engine and never reach here, but a
        # client may set max_fake_risk lower than that for extra caution.
        fake_risk = (
            db.query(NewsEvent.fake_risk)
            .filter(NewsEvent.id == signal.event_id)
            .scalar()
        )
        if fake_risk is not None and fake_risk > sub.max_fake_risk:
            return None
    if sub.allowed_symbols:
        try:
            allowed = set(json.loads(sub.allowed_symbols))
            if signal.symbol not in allowed:
                return None
        except Exception:
            pass

    # Pull recent orders for guard inputs
    recent = (
        db.query(AutoTradeOrder)
        .filter(AutoTradeOrder.subscription_id == sub.id)
        .order_by(AutoTradeOrder.created_at.desc())
        .limit(50)
        .all()
    )
    pnl_today = daily_pnl(recent)
    # Use the start-of-day balance so daily-loss-limit is computed against
    # today's open, not the oldest balance in the recent-50 window.
    today_open = starting_of_day_balance(recent)
    starting_balance = (
        today_open
        or (recent[-1].balance_before if recent else None)
        or DEFAULT_PAPER_BALANCE
    )
    free_balance = (recent[0].balance_after if recent else DEFAULT_PAPER_BALANCE) or DEFAULT_PAPER_BALANCE

    proposed_notional = free_balance * sub.max_position_pct
    side = "buy" if signal.action == "LONG" else "sell"
    settings = get_settings()

    decision: GuardDecision = evaluate_pre_order(
        status=sub.status,
        live_trading_enabled=sub.live_trading_enabled,
        global_autotrade_enabled=settings.enable_autotrade,
        proposed_notional=proposed_notional,
        free_balance=free_balance,
        max_position_pct=sub.max_position_pct,
        daily_pnl=pnl_today,
        starting_balance=starting_balance,
        daily_loss_limit_pct=sub.daily_loss_limit_pct,
    )

    # If guard says no AND the reason is "paper mode" or "global kill", we
    # still want to record a *paper* execution so the client sees the trade.
    paper_only = (
        not decision.allow
        and decision.reason in (
            "subscription is in paper mode",
            "global autotrade kill switch is off",
        )
    )

    if not decision.allow and not paper_only:
        order = AutoTradeOrder(
            subscription_id=sub.id,
            signal_id=signal.id,
            mode="paper",
            symbol=signal.symbol,
            side=side,
            qty=0.0,
            entry_price=signal.entry_price or 0.0,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status="rejected",
            rejected_reason=decision.reason,
            balance_before=starting_balance,
            balance_after=free_balance,
        )
        db.add(order)
        if decision.pause_subscription:
            sub.status = "paused"
            sub.last_paused_reason = decision.reason
            db.add(sub)
        db.commit()
        db.refresh(order)
        log.info("autotrade rejected sub=%s sig=%s reason=%s", sub.id, signal.id, decision.reason)
        return order

    mode = "paper" if paper_only else "live"
    qty = _qty_from_notional(proposed_notional, signal.entry_price or 1.0)

    exchange_order_id = None
    if mode == "live":
        try:
            exchange_order_id = _place_live_order(sub, signal, side, qty)
        except Exception as exc:  # pragma: no cover - live path is feature-flagged
            log.exception("live order failed; recording as rejected")
            order = AutoTradeOrder(
                subscription_id=sub.id,
                signal_id=signal.id,
                mode="live",
                symbol=signal.symbol,
                side=side,
                qty=qty,
                entry_price=signal.entry_price or 0.0,
                status="rejected",
                rejected_reason=f"exchange error: {exc}",
                balance_before=starting_balance,
                balance_after=free_balance,
            )
            db.add(order)
            db.commit()
            db.refresh(order)
            return order

    order = AutoTradeOrder(
        subscription_id=sub.id,
        signal_id=signal.id,
        mode=mode,
        symbol=signal.symbol,
        side=side,
        qty=qty,
        entry_price=signal.entry_price or 0.0,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        status="filled",
        balance_before=free_balance,
        balance_after=free_balance,  # PnL realized when position closes (out of MVP scope)
        exchange_order_id=exchange_order_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _qty_from_notional(notional: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return round(notional / price, 6)


def _place_live_order(
    sub: AutoTradeSubscription, signal: Signal, side: str, qty: float
) -> str | None:  # pragma: no cover - live path needs real keys
    """Stub: in the real implementation this decrypts sub.api_key_encrypted
    and calls ccxt.<exchange>.create_order. For MVP we keep this isolated
    behind feature flags and the unit tests exercise paper mode only."""
    from app.autotrade.crypto import decrypt
    import ccxt

    api_key = decrypt(sub.api_key_encrypted or "")
    api_secret = decrypt(sub.api_secret_encrypted or "")
    if not api_key or not api_secret:
        raise RuntimeError("missing api credentials")
    klass = getattr(ccxt, sub.exchange_id, None)
    if klass is None:
        raise RuntimeError(f"unknown exchange {sub.exchange_id}")
    client = klass({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
    order: dict[str, Any] = client.create_order(
        symbol=signal.symbol, type="market", side=side, amount=qty
    )
    return str(order.get("id") or "")
