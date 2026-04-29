"""Position-sizing calculator (Trading-Risk agent).

Pure-math helper used by both the bot's risk engine and the /app
position-sizing widget. Deterministic — no DB, no I/O.

Inputs (all in account-currency, normally USDT):
  - equity:                 current account equity
  - entry_price:            intended entry price
  - stop_loss_price:        intended stop-loss price (must be on the
                            opposite side of `side`)
  - side:                   "long" | "short"
  - risk_per_trade_pct:     fraction of equity to risk per trade
                            (typical 0.5%–2%)
  - max_position_pct:       hard cap on notional vs equity (defaults to
                            the subscription's `max_position_pct`)
  - daily_loss_remaining:   how much loss budget we still have today
                            (passed in from `risk_guard.daily_pnl`).
                            If <= 0, returns 0 size.

Returns: a dict with `qty`, `notional`, `risk_amount`, `binding_cap`
("risk", "max_position", "daily_loss"), and human-readable notes."""
from __future__ import annotations

from typing import Literal


def calc(
    *,
    equity: float,
    entry_price: float,
    stop_loss_price: float,
    side: Literal["long", "short"],
    risk_per_trade_pct: float = 1.0,
    max_position_pct: float = 25.0,
    daily_loss_remaining: float | None = None,
) -> dict:
    if equity <= 0 or entry_price <= 0 or stop_loss_price <= 0:
        return _empty("invalid inputs (equity / prices must be > 0)")
    if side not in ("long", "short"):
        return _empty("side must be 'long' or 'short'")
    if side == "long" and stop_loss_price >= entry_price:
        return _empty("long: stop_loss must be below entry")
    if side == "short" and stop_loss_price <= entry_price:
        return _empty("short: stop_loss must be above entry")
    if not (0 < risk_per_trade_pct <= 10):
        return _empty("risk_per_trade_pct must be in (0, 10]")
    if not (0 < max_position_pct <= 100):
        return _empty("max_position_pct must be in (0, 100]")

    # Distance to stop, normalized.
    stop_distance = abs(entry_price - stop_loss_price)
    stop_distance_pct = stop_distance / entry_price * 100

    # Risk budget: equity × risk_per_trade_pct.
    risk_amount = equity * risk_per_trade_pct / 100
    qty_by_risk = risk_amount / stop_distance

    # Notional cap: equity × max_position_pct.
    notional_cap = equity * max_position_pct / 100
    qty_by_notional = notional_cap / entry_price

    # Daily-loss cap: don't size a trade that, if it stops out, would
    # blow past the daily-loss budget.
    qty_by_daily = float("inf")
    daily_cap_active = False
    if daily_loss_remaining is not None:
        if daily_loss_remaining <= 0:
            return _empty("daily-loss limit reached for today")
        qty_by_daily = daily_loss_remaining / stop_distance
        daily_cap_active = True

    # Pick the binding constraint.
    candidates: list[tuple[str, float]] = [
        ("risk", qty_by_risk),
        ("max_position", qty_by_notional),
    ]
    if daily_cap_active:
        candidates.append(("daily_loss", qty_by_daily))
    binding, qty = min(candidates, key=lambda x: x[1])

    notional = qty * entry_price
    return {
        "qty": round(qty, 6),
        "notional": round(notional, 2),
        "risk_amount": round(qty * stop_distance, 2),
        "stop_distance_pct": round(stop_distance_pct, 3),
        "binding_cap": binding,
        "notes": _explain(binding, qty_by_risk, qty_by_notional, qty_by_daily),
    }


def _empty(reason: str) -> dict:
    return {
        "qty": 0.0,
        "notional": 0.0,
        "risk_amount": 0.0,
        "stop_distance_pct": 0.0,
        "binding_cap": "invalid",
        "notes": reason,
    }


def _explain(binding: str, qr: float, qn: float, qd: float) -> str:
    if binding == "risk":
        return f"Limited by risk budget. Qty if no notional/daily caps: {qr:.6f}"
    if binding == "max_position":
        return f"Limited by max_position_pct. Risk-only qty would have been {qr:.6f}"
    if binding == "daily_loss":
        return (
            f"Limited by remaining daily-loss budget. Risk-only qty would have been "
            f"{qr:.6f}"
        )
    return ""
