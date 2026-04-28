"""Per-subscription risk guards.

Three independent kill conditions — any one trips ⇒ subscription is paused
(`status="paused"`) and the user must re-enable it manually via the API.

  1. **daily_loss_limit_pct** — sum of realised PnL across orders today
     ≤ -limit% × balance_at_day_start ⇒ pause.
  2. **max_position_pct** — proposed notional > limit% × free balance ⇒
     reject the single order, but keep subscription active.
  3. **kill_switch** — any operator (or the user themselves) can set
     status="killed" via `POST /autotrade/{id}/kill`. Every order check
     returns False until the user re-enables.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardDecision:
    allow: bool
    reason: str = ""
    pause_subscription: bool = False


def evaluate_pre_order(
    *,
    status: str,
    live_trading_enabled: bool,
    global_autotrade_enabled: bool,
    proposed_notional: float,
    free_balance: float,
    max_position_pct: float,
    daily_pnl: float,
    starting_balance: float,
    daily_loss_limit_pct: float,
) -> GuardDecision:
    """Single-call guard used right before placing an order. Returns a
    `GuardDecision` describing whether to proceed and whether to pause the
    subscription afterwards."""
    if status in ("killed", "paused"):
        return GuardDecision(False, f"subscription is {status}")
    if not global_autotrade_enabled:
        return GuardDecision(False, "global autotrade kill switch is off")
    if not live_trading_enabled:
        return GuardDecision(False, "subscription is in paper mode")
    if free_balance <= 0:
        return GuardDecision(False, "non-positive free balance")
    if proposed_notional <= 0:
        return GuardDecision(False, "non-positive notional")

    # Daily-loss check
    if starting_balance > 0:
        loss_pct = -daily_pnl / starting_balance if daily_pnl < 0 else 0.0
        if loss_pct >= daily_loss_limit_pct:
            return GuardDecision(
                False,
                f"daily loss limit hit ({loss_pct:.2%} ≥ {daily_loss_limit_pct:.2%})",
                pause_subscription=True,
            )

    # Max-position check (single-trade cap)
    notional_cap = free_balance * max_position_pct
    if proposed_notional > notional_cap:
        return GuardDecision(
            False,
            f"position size {proposed_notional:.2f} > cap {notional_cap:.2f}"
            f" ({max_position_pct:.2%} of free balance)",
        )

    return GuardDecision(True, "ok")


def starting_of_day_balance(orders: Iterable, now: datetime | None = None) -> float:
    """Best-effort: take the earliest 'balance_before' value among today's
    orders. Falls back to 0 if no orders yet today (caller fills with current
    balance)."""
    if now is None:
        now = datetime.now(timezone.utc)
    day_start = (now - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)).replace(microsecond=0)
    earliest = None
    for o in orders:
        ts = getattr(o, "created_at", None)
        if ts and ts >= day_start.replace(tzinfo=None):
            if earliest is None or ts < earliest.created_at:
                earliest = o
    if earliest is None:
        return 0.0
    return float(getattr(earliest, "balance_before", 0.0) or 0.0)


def daily_pnl(orders: Iterable, now: datetime | None = None) -> float:
    if now is None:
        now = datetime.now(timezone.utc)
    day_start = (now - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)).replace(microsecond=0, tzinfo=None)
    total = 0.0
    for o in orders:
        ts = getattr(o, "created_at", None)
        if ts and ts >= day_start:
            pnl = getattr(o, "realized_pnl", None)
            if pnl is not None:
                total += float(pnl)
    return total
