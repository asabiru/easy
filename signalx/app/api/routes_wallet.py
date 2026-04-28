"""Client wallet endpoints — managed-pool (custody) flow.

  GET  /wallet/me                     — balance, shares, share-price, equity, P&L
  POST /wallet/deposit-address        — allocate per-client USDT address (chain-specific)
  GET  /wallet/deposits               — caller's deposit history
  POST /wallet/withdraw               — request a USDT withdrawal (queued)
  GET  /wallet/withdrawals            — caller's withdrawal history

Compliance posture
------------------
This is a custody flow: SignalX holds client USDT in its own wallets,
trades the aggregated pool, and tracks per-client share ownership.
Until SignalX holds an investment-management / VASP / collective-
investment-scheme licence in the operating jurisdiction, the deposit-
address endpoint refuses with 503 unless the operator has explicitly
opted in via `CUSTODY_SELF_ATTEST_OVERRIDE=true`. The risk text every
client must accept is in `/legal/disclosures.html`.

Hard rules:
  * KYC `enforce=True` on every state-mutating wallet endpoint
    (deposit-address, withdraw) — no `_optional` variants.
  * Withdrawal cooldown of 24h after last deposit prevents flush-
    attacks (CEX-style anti-money-laundering posture).
  * 2FA required for VIP/Auto-Pro tiers before withdraw — same rule
    that gates /autotrade/go-live.
  * All state changes write an `AmlEvent` audit-log entry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.compliance.risk_ack import require_risk_ack
from app.config.settings import get_settings
from app.custody import addresses as addr_mod
from app.custody import shares as shares_math
from app.database.models import (
    AmlEvent,
    ClientWallet,
    Deposit,
    DepositAddress,
    NavSnapshot,
    User,
    Withdrawal,
)
from app.database.session import get_db
from app.kyc.deps import require_kyc

log = logging.getLogger(__name__)
router = APIRouter()


# ────────────────────────────── helpers ────────────────────────────── #


def _get_or_create_wallet(db: Session, user_id: int) -> ClientWallet:
    w = db.query(ClientWallet).filter(ClientWallet.user_id == user_id).first()
    if w is None:
        w = ClientWallet(user_id=user_id, shares=0.0, balance_usdt=0.0, hwm_share_price=1.0)
        db.add(w)
        db.flush()
    return w


def _current_share_price(db: Session) -> float:
    """Latest snapshot's share-price, or 1.0 if pool not bootstrapped."""
    snap = db.query(NavSnapshot).order_by(NavSnapshot.id.desc()).first()
    if snap is None:
        return 1.0
    return snap.share_price


def _equity_usdt(wallet: ClientWallet, share_price: float) -> float:
    return float(wallet.shares) * float(share_price)


def _check_kyc_or_403(db: Session, user: User) -> None:
    """Custody-grade KYC check. Always enforced — overrides the global
    KYC_REQUIRED toggle. Admin role bypasses (admins act through the
    compliance dashboard which has its own audit trail)."""
    from app.database.models import KycProfile

    if user.role == "admin":
        return
    profile = (
        db.query(KycProfile).filter(KycProfile.user_id == user.id).first()
    )
    if profile is None or profile.status != "approved":
        raise HTTPException(
            status_code=403,
            detail="KYC verification required. POST /kyc/start to begin.",
        )
    if profile.sanctions_hit:
        raise HTTPException(
            status_code=403, detail="account blocked by AML screening",
        )


def _audit(db: Session, user_id: int, kind: str, payload: dict[str, Any]) -> None:
    """Append-only AML/audit event. Never raises — best-effort but never silent."""
    import json
    try:
        db.add(AmlEvent(
            user_id=user_id,
            actor_id=user_id,
            kind=kind,
            detail=json.dumps(payload, default=str),
        ))
    except Exception:  # noqa: BLE001 — audit must not break the call path
        log.exception("custody audit write failed")


# ─────────────────────────── GET /wallet/me ─────────────────────────── #


@router.get("/wallet/me")
def wallet_me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    wallet = _get_or_create_wallet(db, user.id)
    db.commit()
    share_price = _current_share_price(db)
    equity = _equity_usdt(wallet, share_price)
    realised_pnl = (
        equity
        + float(wallet.lifetime_withdraw_usdt)
        - float(wallet.lifetime_deposit_usdt)
    )
    return {
        "user_id": user.id,
        "shares": float(wallet.shares),
        "share_price_usdt": share_price,
        "equity_usdt": equity,
        "balance_usdt": float(wallet.balance_usdt),
        "hwm_share_price": float(wallet.hwm_share_price),
        "lifetime_deposit_usdt": float(wallet.lifetime_deposit_usdt),
        "lifetime_withdraw_usdt": float(wallet.lifetime_withdraw_usdt),
        "all_time_pnl_usdt": realised_pnl,
        "last_fee_at": wallet.last_fee_at.isoformat() if wallet.last_fee_at else None,
    }


# ─────────────────────── POST /wallet/deposit-address ─────────────────── #


class DepositAddressIn(BaseModel):
    chain: Literal["trc20", "erc20", "ton", "sol", "bsc"]


@router.post("/wallet/deposit-address")
def deposit_address(
    payload: DepositAddressIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Allocate (or reuse) the client's deposit address for `chain`.

    Refuses with 503 in two cases:
      * `CUSTODY_LIVE_DEPOSITS_ENABLED=false` (default). The address
        is generated deterministically for testing and surfaced ONLY
        when this flag is on, so a misconfigured deploy can't accept
        real money.
      * No licence info AND no operator self-attest override —
        prevents an unlicensed deploy from inadvertently going live.

    The deploy-config gate runs FIRST so the 503 surfaces clearly to
    operations dashboards regardless of the caller's KYC state. KYC
    is enforced afterwards (still mandatory) — see _check_kyc()."""
    s = get_settings()
    if not s.custody_live_deposits_enabled:
        raise HTTPException(
            status_code=503,
            detail="custody deposits are disabled (CUSTODY_LIVE_DEPOSITS_ENABLED=false)",
        )
    has_license = bool(s.custody_license_jurisdiction and s.custody_license_number)
    if not has_license and not s.custody_self_attest_override:
        raise HTTPException(
            status_code=503,
            detail=(
                "operator has not declared a licence and has not enabled "
                "the self-attest override; refusing to issue a deposit address"
            ),
        )
    # Custody-aware risk acknowledgement gate. RISK_ACK_VERSION=2 added
    # the managed-pool / Mode B section; clients on v1 (execution-only
    # ack) MUST re-accept before we can hand them a deposit address.
    # 412 Precondition Failed is the correct surface — the UI catches it
    # and replays /compliance/risk-ack before retrying.
    require_risk_ack(user)
    # KYC is mandatory regardless of `kyc_required` global toggle —
    # this is a custody / fund-flow endpoint.
    _check_kyc_or_403(db, user)

    # Reuse an active address if it already exists. Per-client address
    # is stable so users can paste it into a wallet's contact book.
    addr = (
        db.query(DepositAddress)
        .filter(
            DepositAddress.user_id == user.id,
            DepositAddress.chain == payload.chain,
        )
        .first()
    )
    if addr is None:
        info = addr_mod.allocate_address(user.id, payload.chain)
        addr = DepositAddress(
            user_id=user.id,
            chain=info["chain"],
            external_address=info["address"],
            memo=info["memo"],
            derivation_path=info["derivation_path"],
        )
        db.add(addr)
        _audit(db, user.id, "custody_address_allocated", {
            "chain": info["chain"],
            "address": info["address"],
            "memo": info["memo"],
        })
        db.commit()

    return {
        "chain": addr.chain,
        "address": addr.external_address,
        "memo": addr.memo,
        "min_amount_usdt": addr_mod.MIN_DEPOSIT_USDT[addr.chain],  # type: ignore[index]
        "asset": "USDT",
        "asset_contract": addr_mod.USDT_CONTRACT[addr.chain],  # type: ignore[index]
        "warning": (
            "Send USDT only on the matching chain. Wrong-chain transfers "
            "are unrecoverable."
        ),
    }


# ─────────────────────────── GET /wallet/deposits ─────────────────────── #


@router.get("/wallet/deposits")
def wallet_deposits(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows = (
        db.query(Deposit)
        .filter(Deposit.user_id == user.id)
        .order_by(Deposit.id.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "chain": r.chain,
                "tx_hash": r.tx_hash,
                "amount_usdt": float(r.amount_usdt),
                "credited": bool(r.credited),
                "shares_credited": float(r.shares_credited or 0.0),
                "share_price_at_credit": float(r.share_price_at_credit or 0.0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "credited_at": r.credited_at.isoformat() if r.credited_at else None,
            }
            for r in rows
        ]
    }


# ─────────────────────────── POST /wallet/withdraw ────────────────────── #


class WithdrawIn(BaseModel):
    chain: Literal["trc20", "erc20", "ton", "sol", "bsc"]
    destination_address: str = Field(..., min_length=8, max_length=128)
    amount_usdt: float = Field(..., gt=0)


@router.post("/wallet/withdraw")
def withdraw(
    payload: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    s = get_settings()
    # Same risk-ack + KYC posture as the deposit-address gate above.
    # require_risk_ack runs first because it's the cheapest check and
    # surfaces a 412 the UI knows how to replay.
    require_risk_ack(user)
    # Withdrawals always require KYC — this is a fund-flow endpoint.
    _check_kyc_or_403(db, user)
    if payload.amount_usdt < s.custody_min_withdraw_usdt:
        raise HTTPException(
            status_code=400,
            detail=f"minimum withdrawal is {s.custody_min_withdraw_usdt} USDT",
        )
    wallet = _get_or_create_wallet(db, user.id)
    share_price = _current_share_price(db)
    equity = _equity_usdt(wallet, share_price)
    if payload.amount_usdt > equity:
        raise HTTPException(
            status_code=400,
            detail=f"insufficient balance: equity is {equity:.2f} USDT",
        )

    # Cooldown: no withdraws within `custody_withdraw_cooldown_hours` of
    # last credited deposit. This is the same anti-flush rule major
    # CEXes apply (Bybit / Binance / OKX). Operator can override per-
    # user via the admin treasury panel.
    cooldown = timedelta(hours=int(s.custody_withdraw_cooldown_hours))
    last_dep = (
        db.query(Deposit)
        .filter(Deposit.user_id == user.id, Deposit.credited == True)  # noqa: E712
        .order_by(Deposit.id.desc())
        .first()
    )
    if (
        last_dep is not None
        and last_dep.credited_at is not None
        and datetime.utcnow() - last_dep.credited_at < cooldown
        and user.role != "admin"
    ):
        wait_h = int(
            (cooldown - (datetime.utcnow() - last_dep.credited_at)).total_seconds() // 3600
        )
        raise HTTPException(
            status_code=409,
            detail=f"withdraw cooldown active — try again in ~{wait_h}h",
        )

    # 2FA gate for the higher tiers (mirrors /autotrade/go-live).
    if user.role != "admin" and not user.totp_enabled and equity >= 5000:
        raise HTTPException(
            status_code=403,
            detail="2FA required for withdrawals over 5000 USDT. POST /auth/2fa/setup.",
        )

    shares_to_burn = shares_math.burn_shares(payload.amount_usdt, share_price)
    if shares_to_burn > float(wallet.shares):
        raise HTTPException(
            status_code=400, detail="insufficient share balance",
        )

    wd = Withdrawal(
        user_id=user.id,
        chain=payload.chain,
        destination_address=payload.destination_address.strip(),
        amount_usdt=float(payload.amount_usdt),
        shares_burned=shares_to_burn,
        share_price_at_request=share_price,
        status="queued",
    )
    db.add(wd)
    # Burn shares immediately on request — operator can't unilaterally
    # release shares on cancel without restoring them. /cancel re-credits.
    wallet.shares = float(wallet.shares) - shares_to_burn
    wallet.balance_usdt = _equity_usdt(wallet, share_price)
    db.add(wallet)
    _audit(db, user.id, "custody_withdraw_queued", {
        "chain": payload.chain,
        "destination_address": payload.destination_address,
        "amount_usdt": float(payload.amount_usdt),
        "shares_burned": shares_to_burn,
        "share_price": share_price,
    })
    db.commit()
    db.refresh(wd)
    return {
        "withdrawal_id": wd.id,
        "status": wd.status,
        "amount_usdt": float(wd.amount_usdt),
        "shares_burned": float(wd.shares_burned),
        "share_price_at_request": float(wd.share_price_at_request),
    }


# ─────────────────────── GET /wallet/withdrawals ──────────────────────── #


@router.get("/wallet/withdrawals")
def wallet_withdrawals(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows = (
        db.query(Withdrawal)
        .filter(Withdrawal.user_id == user.id)
        .order_by(Withdrawal.id.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "chain": r.chain,
                "destination_address": r.destination_address,
                "amount_usdt": float(r.amount_usdt),
                "shares_burned": float(r.shares_burned),
                "status": r.status,
                "tx_hash": r.tx_hash,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            }
            for r in rows
        ]
    }
