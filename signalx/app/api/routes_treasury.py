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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
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
    total_shares = float(
        db.query(ClientWallet).with_entities(
            __import__("sqlalchemy").func.coalesce(__import__("sqlalchemy").func.sum(ClientWallet.shares), 0.0)
        ).scalar() or 0.0
    )
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
    return {
        "total_aum_usdt": float(snap.total_aum_usdt) if snap else 0.0,
        "total_shares": total_shares,
        "share_price_usdt": float(snap.share_price) if snap else 1.0,
        "snapshot_at": snap.at.isoformat() if snap else None,
        "by_chain": by_chain,
        "client_count": int(
            db.query(ClientWallet).filter(ClientWallet.shares > 0).count()
        ),
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


@router.post("/admin/treasury/nav/snapshot")
def nav_snapshot(
    payload: NavSnapIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
) -> dict[str, Any]:
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

    # Accrue performance fees for every wallet whose share-price beat
    # its HWM. The fee is taken in shares, so pool AUM is unchanged —
    # only share-of-pool reallocates from clients to the treasury.
    s = get_settings()
    perf_fee_pct = float(s.custody_perf_fee_pct)
    treasury_user_id = None  # no on-chain treasury user yet — fees just accumulate as PerformanceFee rows
    for wallet in db.query(ClientWallet).filter(ClientWallet.shares > 0).all():
        fee_shares, fee_usdt, new_hwm = shares_math.performance_fee_shares(
            user_shares=float(wallet.shares),
            share_price_now=new_price,
            hwm_share_price=float(wallet.hwm_share_price),
            perf_fee_pct=perf_fee_pct,
        )
        if fee_shares > 0:
            wallet.shares = float(wallet.shares) - fee_shares
            wallet.hwm_share_price = new_hwm
            wallet.last_fee_at = datetime.utcnow()
            wallet.balance_usdt = float(wallet.shares) * new_price
            db.add(wallet)
            db.add(PerformanceFee(
                user_id=wallet.user_id,
                kind="performance",
                hwm_before=new_hwm,  # not perfectly accurate; we don't track pre-update HWM separately
                hwm_after=new_hwm,
                share_price=new_price,
                fee_shares=fee_shares,
                fee_usdt_equiv=fee_usdt,
            ))
    _audit(db, None, actor.id, "custody_nav_snapshot", {
        "aum_usdt": float(payload.aum_usdt),
        "total_shares": total_shares,
        "share_price": new_price,
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
    db.commit()
    db.refresh(dep)
    return {
        "id": dep.id,
        "credited": True,
        "shares_credited": float(dep.shares_credited or 0.0),
        "share_price_at_credit": float(dep.share_price_at_credit or 0.0),
        "idempotent": False,
    }
