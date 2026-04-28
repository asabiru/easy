"""Share-accounting math for the managed pool.

Pure functions — no DB, no I/O. Tested directly in
`tests/test_custody.py`. The DB-side wiring lives in
`app/custody/service.py` and `app/api/routes_wallet.py`.

Conventions
-----------

* `share_price` is in USDT per share. Bootstrap value is 1.0
  (first-deposit-ever convention).
* Issuance is always `shares_credited = amount_usdt / share_price`,
  never the inverse — clients pay USDT and receive shares.
* Burns are always `usdt_paid = shares_burned * share_price` for the
  same reason.
* The minimum sentinel `_MIN_SHARE_PRICE = 1e-6` exists only to prevent
  `ZeroDivisionError` from a fully-drawn-down pool. Real-world pool
  drawdowns past 99.9999% are an irrecoverable failure mode that needs
  an operational decision (refund, hold, wind-down) — code falls back
  to the sentinel and surfaces the state via the admin treasury view.
"""
from __future__ import annotations

from typing import Final

_MIN_SHARE_PRICE: Final[float] = 1e-6


def issue_shares(amount_usdt: float, share_price: float) -> float:
    """Compute shares to credit when a client deposits `amount_usdt`.

    Bootstraps to 1.0 share-price if `share_price <= 0` (first-ever
    deposit, no NAV reference yet)."""
    if amount_usdt <= 0:
        return 0.0
    sp = share_price if share_price and share_price > 0 else 1.0
    return amount_usdt / sp


def burn_shares(amount_usdt: float, share_price: float) -> float:
    """Compute shares to burn when a client withdraws `amount_usdt`.

    Caller is responsible for verifying the client owns at least the
    returned share-count; this function only does the math."""
    if amount_usdt <= 0:
        return 0.0
    sp = share_price if share_price and share_price > _MIN_SHARE_PRICE else _MIN_SHARE_PRICE
    return amount_usdt / sp


def share_price_from_aum(total_aum_usdt: float, total_shares: float) -> float:
    """Recompute pool share price.

    Returns 1.0 when there are no shares yet (pool bootstrap).
    """
    if total_shares <= 0:
        return 1.0
    if total_aum_usdt <= 0:
        return _MIN_SHARE_PRICE
    return total_aum_usdt / total_shares


def performance_fee_shares(
    user_shares: float,
    share_price_now: float,
    hwm_share_price: float,
    perf_fee_pct: float = 0.20,
) -> tuple[float, float, float]:
    """Compute performance-fee shares to transfer from a client to the
    treasury wallet on a NAV snapshot.

    Returns `(fee_shares, fee_usdt_equiv, new_hwm)`. When
    `share_price_now <= hwm_share_price`, no fee accrues (drawdown).

    Math: client gain = user_shares * (share_price_now - hwm). Fee = 20%
    of that gain in USDT terms; fee_shares = fee_usdt / share_price_now
    so the treasury's claim is in shares (price-stable across future
    NAV moves until withdrawn)."""
    if user_shares <= 0 or share_price_now <= hwm_share_price:
        return 0.0, 0.0, hwm_share_price
    gain_usdt = user_shares * (share_price_now - hwm_share_price)
    fee_usdt = gain_usdt * perf_fee_pct
    fee_shares = fee_usdt / share_price_now if share_price_now > 0 else 0.0
    return fee_shares, fee_usdt, share_price_now


def management_fee_shares(
    user_balance_usdt: float,
    share_price_now: float,
    days_elapsed: float,
    annual_fee_pct: float = 0.02,
) -> tuple[float, float]:
    """Compute daily-pro-rata management fee in shares.

    `annual_fee_pct=0.02` ⇒ 2%/year. For 1 day on a $10k position →
    10000 * 0.02 / 365 ≈ $0.55. Returns `(fee_shares, fee_usdt)`."""
    if user_balance_usdt <= 0 or days_elapsed <= 0 or share_price_now <= 0:
        return 0.0, 0.0
    fee_usdt = user_balance_usdt * annual_fee_pct * (days_elapsed / 365.0)
    fee_shares = fee_usdt / share_price_now
    return fee_shares, fee_usdt
