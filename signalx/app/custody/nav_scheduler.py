"""NAV-snapshot helper + Celery beat scheduler.

Callers
-------
  * ``POST /admin/treasury/nav/snapshot`` (operator-driven, explicit AUM)
  * Celery beat task ``custody_nav_autosnapshot_task`` (hourly, gated)

Both paths call :func:`apply_nav_snapshot` so fee-accrual, audit, and
share-price math are defined in exactly one place.

Gating
------
The scheduled task reads ``custody_nav_autoschedule_enabled`` + a
stub AUM source (``CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT`` env var). Default
is **off** — the operator still has to supply AUM manually via the
endpoint. The hook exists so that, once we integrate real exchange +
on-chain balance adapters, flipping one env flag turns on autopilot
without touching core fee logic.

What the autoscheduler does NOT do
----------------------------------
  * It does NOT read actual exchange or on-chain balances in MVP. The
    env-var adapter is a stub so the scheduling plumbing can be tested
    without blocking on ccxt / tron-web adapters.
  * It does NOT fail loudly if the flag is off — the task no-ops so it
    is safe to register unconditionally in the beat schedule.
  * It does NOT acquire a distributed lock. Celery beat only emits one
    task per schedule interval per cluster, and the NAV snapshot write
    is a single DB transaction, so concurrent writes are not expected.
    If beat is run on two hosts simultaneously (misconfiguration), the
    worst case is two snapshots with slightly different timestamps —
    not a safety issue.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.custody import shares as shares_math
from app.database.models import (
    AmlEvent,
    ClientWallet,
    NavSnapshot,
    PerformanceFee,
    User,
)

log = logging.getLogger(__name__)

TREASURY_INTERNAL_EMAIL = "treasury@signalx.internal"


@dataclass
class NavSnapshotResult:
    """Return shape for :func:`apply_nav_snapshot`."""

    snapshot_id: int
    at: datetime
    total_aum_usdt: float
    total_shares: float
    share_price: float
    fee_shares_to_treasury: float


def _get_or_create_treasury_wallet(db: Session) -> ClientWallet:
    """Mirror of ``routes_treasury._get_or_create_treasury_wallet``.

    Duplicated here rather than imported so the scheduler module does
    not cyclically depend on the routes module (routes imports this
    helper)."""
    user = db.query(User).filter(User.email == TREASURY_INTERNAL_EMAIL).first()
    if user is None:
        user = User(
            email=TREASURY_INTERNAL_EMAIL,
            password_hash="!disabled-treasury-internal",
            role="admin",
            is_active=False,
        )
        db.add(user)
        db.flush()
    wallet = (
        db.query(ClientWallet)
        .filter(ClientWallet.user_id == user.id)
        .first()
    )
    if wallet is None:
        wallet = ClientWallet(
            user_id=user.id,
            shares=0.0,
            balance_usdt=0.0,
            hwm_share_price=1.0,
        )
        db.add(wallet)
        db.flush()
    return wallet


def apply_nav_snapshot(
    db: Session,
    aum_usdt: float,
    note: str | None,
    actor_id: int | None,
) -> NavSnapshotResult:
    """Insert a NAV snapshot row, accrue performance fees, commit.

    Caller passes an open session — this function issues ``db.commit()``
    exactly once. If the caller needs to rollback on error, they must
    wrap the call in a try/except and call ``db.rollback()``.

    ``actor_id`` is the operator's user id for the audit row, or ``None``
    when the scheduler triggered the snapshot (surfaces in audit as
    "actor_id": null)."""
    s = get_settings()
    perf_fee_pct = float(s.custody_perf_fee_pct)

    total_shares = float(
        db.query(ClientWallet).with_entities(
            __import__("sqlalchemy").func.coalesce(
                __import__("sqlalchemy").func.sum(ClientWallet.shares), 0.0,
            )
        ).scalar() or 0.0
    )
    new_price = shares_math.share_price_from_aum(aum_usdt, total_shares)
    snap = NavSnapshot(
        total_aum_usdt=float(aum_usdt),
        total_shares=total_shares,
        share_price=new_price,
        note=note or None,
    )
    db.add(snap)

    treasury_wallet = _get_or_create_treasury_wallet(db)
    total_fee_shares = 0.0
    client_wallets = (
        db.query(ClientWallet)
        .filter(ClientWallet.shares > 0)
        .filter(ClientWallet.user_id != treasury_wallet.user_id)
        .all()
    )
    for wallet in client_wallets:
        old_hwm = float(wallet.hwm_share_price)
        fee_shares, fee_usdt, new_hwm = shares_math.performance_fee_shares(
            user_shares=float(wallet.shares),
            share_price_now=new_price,
            hwm_share_price=old_hwm,
            perf_fee_pct=perf_fee_pct,
        )
        if fee_shares > 0:
            wallet.shares = float(wallet.shares) - fee_shares
            wallet.hwm_share_price = new_hwm
            wallet.last_fee_at = datetime.utcnow()
            wallet.balance_usdt = float(wallet.shares) * new_price
            db.add(wallet)
            total_fee_shares += fee_shares
            db.add(PerformanceFee(
                user_id=wallet.user_id,
                kind="performance",
                hwm_before=old_hwm,
                hwm_after=new_hwm,
                share_price=new_price,
                fee_shares=fee_shares,
                fee_usdt_equiv=fee_usdt,
            ))
    if total_fee_shares > 0:
        treasury_wallet.shares = float(treasury_wallet.shares) + total_fee_shares
        treasury_wallet.balance_usdt = float(treasury_wallet.shares) * new_price
        db.add(treasury_wallet)

    try:
        db.add(AmlEvent(
            user_id=None,
            actor_id=actor_id,
            kind="custody_nav_snapshot",
            detail=json.dumps({
                "aum_usdt": float(aum_usdt),
                "total_shares": total_shares,
                "share_price": new_price,
                "fee_shares_to_treasury": total_fee_shares,
                "note": note,
                "scheduled": actor_id is None,
            }, default=str),
        ))
    except Exception:  # noqa: BLE001
        log.exception("nav-snapshot audit write failed")

    db.commit()
    db.refresh(snap)
    return NavSnapshotResult(
        snapshot_id=snap.id,
        at=snap.at,
        total_aum_usdt=float(snap.total_aum_usdt),
        total_shares=float(snap.total_shares),
        share_price=float(snap.share_price),
        fee_shares_to_treasury=total_fee_shares,
    )


def read_aum_from_adapter() -> float | None:
    """Stub AUM source for the autoscheduled NAV task.

    MVP adapter: reads ``CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT`` as a float.
    Returns ``None`` if unset — the scheduler task will log + no-op,
    which is the safe default (we never want to silently snapshot at
    AUM=0 and wipe out every HWM).

    When the real exchange + on-chain balance adapters land, this
    function becomes the single integration point."""
    import os
    raw = os.environ.get("CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT is not a float: %r", raw)
        return None
