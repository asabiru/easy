"""Admin treasury endpoints — managed-pool operator surface.

  GET  /admin/treasury/pool                — total AUM, total shares, share-price, by-chain breakdown
  GET  /admin/treasury/withdrawals/queue   — queued / approved withdrawals
  POST /admin/treasury/withdrawals/{id}/approve
  POST /admin/treasury/withdrawals/{id}/send
  POST /admin/treasury/withdrawals/{id}/cancel
  POST /admin/treasury/nav/snapshot        — recompute share-price from operator-supplied AUM
  POST /admin/treasury/credit-deposit      — manual deposit credit (MVP, before chain webhooks)

Operator workflow (MVP, before chain webhooks)
----------------------------------------------
1. Client receives an address from /wallet/deposit-address.
2. Client sends USDT to it, sees TX confirmed in their wallet UI.
3. Operator monitors treasury wallets manually, sees the TX, calls
   POST /admin/treasury/credit-deposit with (user_id, chain, tx_hash,
   amount_usdt). Endpoint records the Deposit row and credits shares.
4. Once a day (or any time AUM materially changes), operator calls
   POST /admin/treasury/nav/snapshot {aum_usdt} to refresh share-price.
5. Withdrawals: client requests via /wallet/withdraw → queued. Operator
   reviews destination address, AML-screens, calls /approve, then
   physically signs+broadcasts the on-chain TX, then calls /send with
   tx_hash.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.config.settings import get_settings
from app.custody import shares as shares_math
from app.database.models import (
    AmlEvent,
    ClientWallet,
    Deposit,
    NavSnapshot,
    PerformanceFee,
    User,
    Withdrawal,
)
from app.database.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()

TREASURY_INTERNAL_EMAIL = "treasury@signalx.internal"


def _audit(db: Session, user_id: int | None, actor_id: int, kind: str, payload: dict[str, Any]) -> None:
    try:
        db.add(AmlEvent(
            user_id=user_id,
            actor_id=actor_id,
            kind=kind,
            detail=json.dumps(payload, default=str),
        ))
    except Exception:  # noqa: BLE001
        log.exception("treasury audit write failed")


def _latest_share_price(db: Session) -> float:
    snap = db.query(NavSnapshot).order_by(NavSnapshot.id.desc()).first()
    return float(snap.share_price) if snap else 1.0


# ───────────────────────── GET /admin/treasury/pool ───────────────────── #


@router.get("/admin/treasury/pool")
def pool_overview(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
) -> dict[str, Any]:
    snap = db.query(NavSnapshot).order_by(NavSnapshot.id.desc()).first()
    # `total_shares` includes the internal treasury wallet (which holds
    # accrued fee shares). That's intentional: AUM is divided by ALL
    # outstanding shares including treasury, otherwise burning fee shares
    # would make the share price drift upward for clients.
    total_shares = float(
        db.query(ClientWallet).with_entities(
            __import__("sqlalchemy").func.coalesce(__import__("sqlalchemy").func.sum(ClientWallet.shares), 0.0)
        ).scalar() or 0.0
    )
    # Treasury fee shares — surfaced separately so the operator can see
    # accrued-but-uncollected fees at a glance.
    treasury_user = db.query(User).filter(User.email == TREASURY_INTERNAL_EMAIL).first()
    treasury_shares = 0.0
    if treasury_user:
        tw = db.query(ClientWallet).filter(ClientWallet.user_id == treasury_user.id).first()
        treasury_shares = float(tw.shares) if tw else 0.0
    by_chain: dict[str, dict[str, float]] = {}
    for chain in ("trc20", "erc20", "ton", "sol", "bsc"):
        cred = (
            db.query(Deposit)
            .filter(Deposit.chain == chain, Deposit.credited == True)  # noqa: E712
            .all()
        )
        wd = (
            db.query(Withdrawal)
            .filter(Withdrawal.chain == chain, Withdrawal.status == "sent")
            .all()
        )
        by_chain[chain] = {
            "total_in_usdt": sum(float(d.amount_usdt) for d in cred),
            "total_out_usdt": sum(float(w.amount_usdt) for w in wd),
            "deposit_count": len(cred),
            "withdraw_count": len(wd),
        }
    client_q = db.query(ClientWallet).filter(ClientWallet.shares > 0)
    if treasury_user:
        client_q = client_q.filter(ClientWallet.user_id != treasury_user.id)
    return {
        "total_aum_usdt": float(snap.total_aum_usdt) if snap else 0.0,
        "total_shares": total_shares,
        "treasury_fee_shares": treasury_shares,
        "share_price_usdt": float(snap.share_price) if snap else 1.0,
        "snapshot_at": snap.at.isoformat() if snap else None,
        "by_chain": by_chain,
        "client_count": int(client_q.count()),
    }


# ──────────────────── GET /admin/treasury/withdrawals/queue ───────────── #


@router.get("/admin/treasury/withdrawals/queue")
def withdrawals_queue(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
) -> dict[str, Any]:
    rows = (
        db.query(Withdrawal)
        .filter(Withdrawal.status.in_(("queued", "approved")))
        .order_by(Withdrawal.id.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "chain": r.chain,
                "destination_address": r.destination_address,
                "amount_usdt": float(r.amount_usdt),
                "shares_burned": float(r.shares_burned),
                "share_price_at_request": float(r.share_price_at_request),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                "approved_by_user_id": r.approved_by_user_id,
            }
            for r in rows
        ]
    }


# ───────── POST /admin/treasury/withdrawals/{id}/approve ─ /send ─ /cancel ─ #


class SendIn(BaseModel):
    tx_hash: str = Field(..., min_length=4, max_length=128)


class CancelIn(BaseModel):
    reason: str = Field(..., min_length=4, max_length=512)


@router.post("/admin/treasury/withdrawals/{wd_id}/approve")
def withdrawal_approve(
    wd_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    wd = db.query(Withdrawal).filter(Withdrawal.id == wd_id).first()
    if wd is None:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    if wd.status != "queued":
        raise HTTPException(status_code=409, detail=f"withdrawal is {wd.status}")
    wd.status = "approved"
    wd.approved_at = datetime.utcnow()
    wd.approved_by_user_id = actor.id
    db.add(wd)
    _audit(db, wd.user_id, actor.id, "custody_withdraw_approved", {
        "withdrawal_id": wd.id, "amount_usdt": float(wd.amount_usdt),
    })
    db.commit()
    return {"id": wd.id, "status": wd.status}


@router.post("/admin/treasury/withdrawals/{wd_id}/send")
def withdrawal_send(
    wd_id: int,
    payload: SendIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    wd = db.query(Withdrawal).filter(Withdrawal.id == wd_id).first()
    if wd is None:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    if wd.status != "approved":
        raise HTTPException(
            status_code=409, detail=f"withdrawal must be approved first (is {wd.status})",
        )
    wd.status = "sent"
    wd.sent_at = datetime.utcnow()
    wd.tx_hash = payload.tx_hash.strip()
    db.add(wd)
    # Credit lifetime_withdraw on the wallet for accurate P&L on /wallet/me.
    wallet = (
        db.query(ClientWallet)
        .filter(ClientWallet.user_id == wd.user_id)
        .first()
    )
    if wallet:
        wallet.lifetime_withdraw_usdt = (
            float(wallet.lifetime_withdraw_usdt) + float(wd.amount_usdt)
        )
        db.add(wallet)
    _audit(db, wd.user_id, actor.id, "custody_withdraw_sent", {
        "withdrawal_id": wd.id,
        "tx_hash": payload.tx_hash,
        "amount_usdt": float(wd.amount_usdt),
    })
    db.commit()
    return {"id": wd.id, "status": wd.status, "tx_hash": wd.tx_hash}


@router.post("/admin/treasury/withdrawals/{wd_id}/cancel")
def withdrawal_cancel(
    wd_id: int,
    payload: CancelIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    wd = db.query(Withdrawal).filter(Withdrawal.id == wd_id).first()
    if wd is None:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    if wd.status not in ("queued", "approved"):
        raise HTTPException(status_code=409, detail=f"withdrawal is {wd.status}")
    # Re-credit shares back to the user (we burned them on /withdraw).
    wallet = (
        db.query(ClientWallet)
        .filter(ClientWallet.user_id == wd.user_id)
        .first()
    )
    if wallet:
        wallet.shares = float(wallet.shares) + float(wd.shares_burned)
        wallet.balance_usdt = float(wallet.shares) * _latest_share_price(db)
        db.add(wallet)
    wd.status = "cancelled"
    wd.cancelled_reason = payload.reason
    db.add(wd)
    _audit(db, wd.user_id, actor.id, "custody_withdraw_cancelled", {
        "withdrawal_id": wd.id, "reason": payload.reason,
        "shares_re_credited": float(wd.shares_burned),
    })
    db.commit()
    return {"id": wd.id, "status": wd.status}


# ─────────────────── POST /admin/treasury/nav/snapshot ────────────────── #


class NavSnapIn(BaseModel):
    """Operator-supplied total AUM in USDT.

    `aum_usdt` includes: USDT in our exchange accounts + open
    positions marked-to-market + USDT in cold/hot treasury wallets,
    minus any pending fee transfers. The operator computes this
    out-of-band (will be automated once the exchange-account adapter
    lands).
    """

    aum_usdt: float = Field(..., ge=0)
    note: str = Field(default="", max_length=256)


def _get_or_create_treasury_wallet(db: Session) -> ClientWallet:
    """Internal-user wallet that holds performance + management fee shares.

    Fees are share-transfers (burn-from-client + credit-to-treasury), not
    burns. Without crediting them somewhere, total_shares would silently
    shrink while AUM stayed constant, causing the share-price denominator
    to drift upward on every snapshot. Holding fees in a wallet keeps
    `sum(shares)` invariant so AUM/shares is stable.

    The treasury wallet is owned by an internal `User` row with a
    non-routable email and `is_active=False` (so the user cannot log in
    or appear in client-facing queries). Created on first fee accrual.
    """
    user = db.query(User).filter(User.email == TREASURY_INTERNAL_EMAIL).first()
    if user is None:
        # is_active=False → admin queries on real users still skip this
        # row; password_hash is unusable so login is impossible.
        user = User(
            email=TREASURY_INTERNAL_EMAIL,
            password_hash="!disabled-treasury-internal",
            role="admin",
            is_active=False,
        )
        db.add(user)
        db.flush()  # need user.id for the wallet FK before commit
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


@router.post("/admin/treasury/nav/snapshot")
def nav_snapshot(
    payload: NavSnapIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    # `total_shares` is invariant across the fee loop: fee-shares are
    # transferred (client → treasury wallet), not burned, so the sum
    # stays constant and AUM/total_shares stays stable. We compute it
    # once and reuse it for both the snapshot row and the fee math.
    total_shares = float(
        db.query(ClientWallet).with_entities(
            __import__("sqlalchemy").func.coalesce(__import__("sqlalchemy").func.sum(ClientWallet.shares), 0.0)
        ).scalar() or 0.0
    )
    new_price = shares_math.share_price_from_aum(payload.aum_usdt, total_shares)
    snap = NavSnapshot(
        total_aum_usdt=float(payload.aum_usdt),
        total_shares=total_shares,
        share_price=new_price,
        note=payload.note or None,
    )
    db.add(snap)

    # Accrue performance fees for every CLIENT wallet whose share-price
    # beat its HWM. The fee is taken in shares, transferred from the
    # client wallet to the internal treasury wallet, so pool AUM is
    # unchanged — only share-of-pool reallocates.
    s = get_settings()
    perf_fee_pct = float(s.custody_perf_fee_pct)
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
    _audit(db, None, actor.id, "custody_nav_snapshot", {
        "aum_usdt": float(payload.aum_usdt),
        "total_shares": total_shares,
        "share_price": new_price,
        "fee_shares_to_treasury": total_fee_shares,
        "note": payload.note,
    })
    db.commit()
    db.refresh(snap)
    return {
        "id": snap.id,
        "at": snap.at.isoformat(),
        "total_aum_usdt": float(snap.total_aum_usdt),
        "total_shares": float(snap.total_shares),
        "share_price_usdt": float(snap.share_price),
    }


# ───────────────── POST /admin/treasury/credit-deposit ────────────────── #


class CreditIn(BaseModel):
    user_id: int
    chain: str = Field(..., min_length=2, max_length=16)
    tx_hash: str = Field(..., min_length=4, max_length=128)
    amount_usdt: float = Field(..., gt=0)


@router.post("/admin/treasury/credit-deposit")
def credit_deposit(
    payload: CreditIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Manually credit a confirmed on-chain deposit. Idempotent on
    `(chain, tx_hash)` — re-calling with the same TX returns the
    existing row without double-issuing shares."""
    target = db.query(User).filter(User.id == payload.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    existing = (
        db.query(Deposit)
        .filter(Deposit.chain == payload.chain, Deposit.tx_hash == payload.tx_hash)
        .first()
    )
    if existing is not None:
        return {
            "id": existing.id,
            "credited": bool(existing.credited),
            "shares_credited": float(existing.shares_credited or 0.0),
            "share_price_at_credit": float(existing.share_price_at_credit or 0.0),
            "idempotent": True,
        }

    share_price = _latest_share_price(db)
    shares = shares_math.issue_shares(payload.amount_usdt, share_price)

    dep = Deposit(
        user_id=payload.user_id,
        chain=payload.chain,
        tx_hash=payload.tx_hash.strip(),
        amount_usdt=float(payload.amount_usdt),
        confirmed_at=datetime.utcnow(),
        credited=True,
        credited_at=datetime.utcnow(),
        share_price_at_credit=share_price,
        shares_credited=shares,
    )
    db.add(dep)

    wallet = (
        db.query(ClientWallet)
        .filter(ClientWallet.user_id == payload.user_id)
        .first()
    )
    if wallet is None:
        wallet = ClientWallet(
            user_id=payload.user_id,
            shares=0.0,
            balance_usdt=0.0,
            hwm_share_price=share_price,
        )
        db.add(wallet)
        db.flush()
    wallet.shares = float(wallet.shares) + shares
    # First-deposit-ever HWM is the entry price; later deposits don't
    # raise HWM (otherwise late entrants would dodge fees on prior gains
    # they didn't fund). HWM never decreases either.
    if wallet.hwm_share_price is None or float(wallet.hwm_share_price) <= 0:
        wallet.hwm_share_price = share_price
    wallet.lifetime_deposit_usdt = (
        float(wallet.lifetime_deposit_usdt) + float(payload.amount_usdt)
    )
    wallet.balance_usdt = float(wallet.shares) * share_price
    db.add(wallet)

    _audit(db, payload.user_id, actor.id, "custody_deposit_credited", {
        "chain": payload.chain,
        "tx_hash": payload.tx_hash,
        "amount_usdt": float(payload.amount_usdt),
        "shares_credited": shares,
        "share_price": share_price,
    })
    # Race protection (defence-in-depth — admin endpoint is rare/sync,
    # but the composite UniqueConstraint on Deposit(chain, tx_hash) will
    # raise IntegrityError if a webhook delivery sneaks in between the
    # existence check above and this commit. Translate to idempotent return.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(Deposit)
            .filter(Deposit.chain == payload.chain, Deposit.tx_hash == payload.tx_hash)
            .first()
        )
        if existing is None:
            raise
        return {
            "id": existing.id,
            "credited": bool(existing.credited),
            "shares_credited": float(existing.shares_credited or 0.0),
            "share_price_at_credit": float(existing.share_price_at_credit or 0.0),
            "idempotent": True,
        }
    db.refresh(dep)
    return {
        "id": dep.id,
        "credited": True,
        "shares_credited": float(dep.shares_credited or 0.0),
        "share_price_at_credit": float(dep.share_price_at_credit or 0.0),
        "idempotent": False,
    }


# ─────────────────── GET /admin/treasury/deposits/pending ────────────── #


@router.get("/admin/treasury/deposits/pending")
def deposits_pending(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
    limit: int = 200,
) -> dict[str, Any]:
    """All uncredited deposits — operator review queue.

    Includes:
      * Deposits routed to the `unassigned@signalx.internal` sentinel
        because the inbound webhook couldn't match a memo or address.
      * Deposits below the chain-specific minimum (gas-floor protection).

    Operator clears the queue via `POST /admin/treasury/credit-deposit`
    (for orphans matched manually via TX inspection) or
    `POST /admin/treasury/deposits/{id}/reattribute` (for sentinel-
    routed orphans where the operator has confirmed the real user)."""
    sentinel = (
        db.query(User)
        .filter(User.email == "unassigned@signalx.internal")
        .first()
    )
    sentinel_id = sentinel.id if sentinel else None
    rows = (
        db.query(Deposit)
        .filter(Deposit.credited == False)  # noqa: E712
        .order_by(Deposit.id.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "is_unattributed": r.user_id == sentinel_id if sentinel_id else False,
                "chain": r.chain,
                "tx_hash": r.tx_hash,
                "amount_usdt": float(r.amount_usdt),
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "sentinel_user_id": sentinel_id,
    }


# ──────── POST /admin/treasury/deposits/{id}/reattribute ──────────── #


class ReattributeIn(BaseModel):
    """Move an uncredited deposit (typically routed to the unassigned
    sentinel by the webhook) onto a real user and credit shares.

    `note` is mandatory — operator MUST justify the reattribution in
    one or two sentences for the audit trail. We pin AML-grade
    accountability on every share-issuance event."""

    user_id: int
    note: str = Field(..., min_length=4, max_length=512)


@router.post("/admin/treasury/deposits/{deposit_id}/reattribute")
def deposit_reattribute(
    deposit_id: int,
    payload: ReattributeIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Reattribute an uncredited deposit to a real user, credit shares.

    Refuses if:
      * Deposit is already credited (409).
      * Target user does not exist or is the sentinel/treasury internal
        account (404 / 400 — never credit shares to internal users via
        this path; operator should use `/credit-deposit` for one-offs).
      * Amount is below chain minimum (409 — operator should bundle
        with a complementary deposit, not silently issue under-min
        shares)."""
    from app.custody.addresses import MIN_DEPOSIT_USDT
    from app.custody.webhooks import UNATTRIBUTED_INTERNAL_EMAIL

    dep = db.query(Deposit).filter(Deposit.id == deposit_id).first()
    if dep is None:
        raise HTTPException(status_code=404, detail="deposit not found")
    if dep.credited:
        raise HTTPException(
            status_code=409, detail="deposit already credited; nothing to reattribute",
        )
    target = db.query(User).filter(User.id == payload.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="target user not found")
    if target.email in (UNATTRIBUTED_INTERNAL_EMAIL, TREASURY_INTERNAL_EMAIL):
        raise HTTPException(
            status_code=400,
            detail="cannot reattribute to an internal sentinel user",
        )
    min_amt = MIN_DEPOSIT_USDT.get(dep.chain, 1.0)
    if float(dep.amount_usdt) < min_amt:
        raise HTTPException(
            status_code=409,
            detail=f"deposit below chain minimum ({min_amt} USDT for {dep.chain})",
        )

    share_price = _latest_share_price(db)
    shares = shares_math.issue_shares(float(dep.amount_usdt), share_price)

    # Re-anchor the deposit row.
    previous_user_id = dep.user_id
    dep.user_id = target.id
    dep.credited = True
    dep.credited_at = datetime.utcnow()
    dep.share_price_at_credit = share_price
    dep.shares_credited = shares
    db.add(dep)

    # Credit the wallet — same logic as `credit_deposit` happy path.
    wallet = (
        db.query(ClientWallet).filter(ClientWallet.user_id == target.id).first()
    )
    if wallet is None:
        wallet = ClientWallet(
            user_id=target.id,
            shares=0.0,
            balance_usdt=0.0,
            hwm_share_price=share_price,
        )
        db.add(wallet)
        db.flush()
    wallet.shares = float(wallet.shares) + shares
    if wallet.hwm_share_price is None or float(wallet.hwm_share_price) <= 0:
        wallet.hwm_share_price = share_price
    wallet.lifetime_deposit_usdt = (
        float(wallet.lifetime_deposit_usdt) + float(dep.amount_usdt)
    )
    wallet.balance_usdt = float(wallet.shares) * share_price
    db.add(wallet)

    _audit(db, target.id, actor.id, "custody_deposit_reattributed", {
        "deposit_id": dep.id,
        "from_user_id": previous_user_id,
        "to_user_id": target.id,
        "chain": dep.chain,
        "tx_hash": dep.tx_hash,
        "amount_usdt": float(dep.amount_usdt),
        "shares_credited": shares,
        "share_price": share_price,
        "operator_note": payload.note,
    })
    db.commit()
    db.refresh(dep)
    return {
        "id": dep.id,
        "user_id": dep.user_id,
        "credited": True,
        "shares_credited": shares,
        "share_price_at_credit": share_price,
    }


# ────────────────── GET /admin/treasury/health ─────────────────── #


@router.get("/admin/treasury/health")
def treasury_health(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
) -> dict[str, Any]:
    """Read-only invariant check.

    Surfaces issues an operator should triage before processing any
    new deposit/withdrawal:

      * `shares_invariant_ok` — sum(wallets.shares) ≈ latest NAV
        snapshot's total_shares within 1e-4 tolerance. False = NAV
        out-of-date or accounting bug.
      * `negative_balances` — count of wallets with balance_usdt < 0
        or shares < 0 (should always be 0).
      * `pending_deposit_count` — uncredited deposits awaiting
        operator action.
      * `unattributed_count` — subset of pending routed to sentinel.
      * `pending_withdrawal_count` — `queued`/`approved` withdrawals.
      * `latest_nav_age_hours` — hours since last NAV snapshot. Stale
        NAV (>24h) means new deposits issue shares at a stale price
        — operator should re-snapshot before crediting big deposits.

    Returns an `overall_status` of `ok` / `warn` / `critical` so a
    monitoring system can page on `critical` without parsing fields."""
    from datetime import datetime, timedelta

    wallets = db.query(ClientWallet).all()
    sum_shares = sum(float(w.shares or 0.0) for w in wallets)
    negatives = sum(
        1 for w in wallets
        if float(w.shares or 0.0) < 0 or float(w.balance_usdt or 0.0) < 0
    )

    latest_nav = (
        db.query(NavSnapshot).order_by(NavSnapshot.id.desc()).first()
    )
    invariant_ok = True
    invariant_drift = 0.0
    nav_age_hours: float | None = None
    if latest_nav is not None:
        invariant_drift = abs(sum_shares - float(latest_nav.total_shares or 0.0))
        invariant_ok = invariant_drift < 1e-4
        if latest_nav.at:
            nav_age_hours = (datetime.utcnow() - latest_nav.at).total_seconds() / 3600.0

    sentinel = (
        db.query(User)
        .filter(User.email == "unassigned@signalx.internal")
        .first()
    )
    sentinel_id = sentinel.id if sentinel else None
    pending_dep = (
        db.query(Deposit).filter(Deposit.credited == False).count()  # noqa: E712
    )
    unattributed = (
        db.query(Deposit)
        .filter(Deposit.credited == False, Deposit.user_id == sentinel_id)  # noqa: E712
        .count()
        if sentinel_id is not None
        else 0
    )
    pending_wd = (
        db.query(Withdrawal)
        .filter(Withdrawal.status.in_(("queued", "approved")))
        .count()
    )

    # Webhook signature-failure spike: a hostile party probing for a
    # webhook secret leaves a trail of custody_deposit_webhook_signature_invalid
    # rows. >= 10 in the last hour is highly anomalous (legitimate
    # provider with a stale secret would error continuously, but an
    # operator should still be paged to rotate the secret regardless).
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    sig_failures_1h = (
        db.query(AmlEvent)
        .filter(
            AmlEvent.kind == "custody_deposit_webhook_signature_invalid",
            AmlEvent.created_at >= one_hour_ago,
        )
        .count()
    )

    # Aggregate status. `critical` triggers paging.
    if negatives > 0 or not invariant_ok or sig_failures_1h >= 10:
        status = "critical"
    elif (
        (nav_age_hours is not None and nav_age_hours > 24)
        or unattributed > 0
        or pending_wd >= 5
        or sig_failures_1h >= 3
    ):
        status = "warn"
    else:
        status = "ok"

    return {
        "overall_status": status,
        "shares_invariant_ok": invariant_ok,
        "shares_invariant_drift": invariant_drift,
        "sum_client_shares": sum_shares,
        "latest_nav_total_shares": float(latest_nav.total_shares) if latest_nav else None,
        "negative_balances": negatives,
        "pending_deposit_count": pending_dep,
        "unattributed_count": unattributed,
        "pending_withdrawal_count": pending_wd,
        "latest_nav_age_hours": nav_age_hours,
        "webhook_signature_failures_1h": sig_failures_1h,
    }


# ───────────────── GET /admin/treasury/audit-log ────────────────── #


@router.get("/admin/treasury/audit-log")
def audit_log(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
    limit: int = 200,
    kind: str | None = None,
) -> dict[str, Any]:
    """Append-only custody audit trail.

    Filter by `kind` to narrow to a specific event class —
    `custody_deposit_credited`, `custody_deposit_webhook_credited`,
    `custody_deposit_webhook_pending_review`,
    `custody_deposit_reattributed`, `custody_withdraw_*`,
    `custody_nav_snapshot`, etc.

    `limit` is hard-clamped to 1000 to keep response sizes sane —
    paging via `before_id` is on the roadmap when an operator hits this
    in anger."""
    q = db.query(AmlEvent).filter(AmlEvent.kind.like("custody_%"))
    if kind:
        q = q.filter(AmlEvent.kind == kind)
    rows = q.order_by(AmlEvent.id.desc()).limit(max(1, min(limit, 1000))).all()
    return {
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "user_id": r.user_id,
                "actor_id": r.actor_id,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ───────────────── CSV exports (compliance / reconcile) ──────────── #


def _csv_response(rows, header, filename: str) -> StreamingResponse:
    """Stream a CSV body with the right headers + RFC-4180-ish quoting.

    `rows` is an iterable of tuples in the same order as `header`.
    Empty fields render as empty strings (not "None"). Datetime values
    are ISO-formatted upstream — this helper just stringifies."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


@router.get("/admin/treasury/audit-log.csv")
def audit_log_csv(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
    limit: int = 1000,
    kind: str | None = None,
) -> StreamingResponse:
    """CSV mirror of /admin/treasury/audit-log.

    Compliance / regulator submissions usually require a flat file —
    this avoids the operator having to JSON→CSV-pivot manually. Same
    filter knobs as the JSON endpoint; clamp at 5000 rows since CSVs
    are typically opened in Excel which chokes above ~1M rows but is
    fine here. `kind` filter accepts the exact event-kind string."""
    q = db.query(AmlEvent).filter(AmlEvent.kind.like("custody_%"))
    if kind:
        q = q.filter(AmlEvent.kind == kind)
    rows = q.order_by(AmlEvent.id.desc()).limit(max(1, min(limit, 5000))).all()
    return _csv_response(
        (
            (
                r.id,
                r.created_at.isoformat() if r.created_at else "",
                r.kind,
                r.user_id if r.user_id is not None else "",
                r.actor_id if r.actor_id is not None else "",
                (r.detail or "").replace("\r", " ").replace("\n", " "),
            )
            for r in rows
        ),
        ("id", "created_at", "kind", "user_id", "actor_id", "detail"),
        f"signalx-custody-audit-{datetime.utcnow().strftime('%Y%m%d')}.csv",
    )


@router.get("/admin/treasury/wallets.csv")
def wallets_csv(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin", "manager")),
) -> StreamingResponse:
    """All client wallets — for daily reconciliation against the
    on-chain treasury balances + exchange account balances. Excludes
    the internal sentinel/treasury users (no client equity to report
    on those rows)."""
    internal = (
        db.query(User)
        .filter(User.email.in_((TREASURY_INTERNAL_EMAIL, "unassigned@signalx.internal")))
        .all()
    )
    internal_ids = {u.id for u in internal}
    wallets = db.query(ClientWallet).all()
    sp = _latest_share_price(db)
    return _csv_response(
        (
            (
                w.user_id,
                float(w.shares or 0.0),
                float(w.balance_usdt or 0.0),
                float(w.hwm_share_price or 1.0),
                float(w.lifetime_deposit_usdt or 0.0),
                float(w.lifetime_withdraw_usdt or 0.0),
                float(w.shares or 0.0) * sp,
                w.last_fee_at.isoformat() if w.last_fee_at else "",
            )
            for w in wallets
            if w.user_id not in internal_ids
        ),
        (
            "user_id", "shares", "balance_usdt", "hwm_share_price",
            "lifetime_deposit_usdt", "lifetime_withdraw_usdt",
            "current_equity_usdt", "last_fee_at",
        ),
        f"signalx-custody-wallets-{datetime.utcnow().strftime('%Y%m%d')}.csv",
    )
