"""Chain-deposit webhook endpoints (managed-pool flow).

Five endpoints — one per chain — all of which:
  * Are gated on `CUSTODY_LIVE_DEPOSITS_ENABLED=true` AND a chain-
    specific webhook secret being configured.
  * Verify the provider's signature scheme via `app/custody/webhooks.py`.
  * Idempotently credit on `(chain, tx_hash)` via the shared
    `record_inbound_deposit()` helper.
  * Always return 200 once signature is verified, even if the deposit
    is below-min or unmatched — providers retry on non-2xx and we don't
    want stuck retry loops for ops decisions.

Endpoints (all POST, all under `/payments/`):
  * `/payments/trongrid/webhook`     — TRC20 USDT
  * `/payments/alchemy/webhook`      — ERC20 USDT
  * `/payments/helius/webhook`       — Solana SPL USDT
  * `/payments/bsc/webhook`          — BEP20 USDT (custom listener)
  * `/payments/ton-pool/webhook`     — TON jetton USDT (managed-pool)

The existing `/payments/wallet-pay/*` (subscription billing) is
separate from these — those route to the autotrade subscription
ledger, not the custody pool.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.custody.webhooks import (
    WebhookError,
    parse_alchemy,
    parse_bscscan,
    parse_helius,
    parse_ton,
    parse_trongrid,
    record_inbound_deposit,
)
from app.database.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()


def _to_response(result, parsed) -> dict[str, Any]:
    """Provider-friendly 200 response. We surface the deposit id +
    credit status so ops can correlate via the audit log without
    leaking internal user state."""
    return {
        "ok": True,
        "deposit_id": result.deposit_id,
        "credited": result.credited,
        "idempotent": result.idempotent,
        "chain": parsed.chain,
        "tx_hash": parsed.tx_hash,
    }


# ────────────────────────── TRC20 (TronGrid) ────────────────────── #


@router.post("/payments/trongrid/webhook")
async def trongrid_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """TronGrid signature: hex HMAC-SHA256 over raw body, header
    `X-TronGrid-Signature`."""
    body = await request.body()
    sig = request.headers.get("x-trongrid-signature", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        parsed = parse_trongrid(body, sig, payload or {})
    except WebhookError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ────────────────────────── ERC20 (Alchemy) ────────────────────── #


@router.post("/payments/alchemy/webhook")
async def alchemy_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Alchemy `Address Activity`: header `X-Alchemy-Signature`, hex
    HMAC-SHA256 over raw body. Signing key set in Alchemy webhook UI."""
    body = await request.body()
    sig = request.headers.get("x-alchemy-signature", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        parsed = parse_alchemy(body, sig, payload or {})
    except WebhookError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ─────────────────────────── Solana (Helius) ────────────────────── #


@router.post("/payments/helius/webhook")
async def helius_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Helius enhanced-webhook: bearer token in `Authorization` header."""
    auth = request.headers.get("authorization", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        parsed = parse_helius(auth, payload or [])
    except WebhookError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ─────────────────────────── BSC (custom) ───────────────────────── #


@router.post("/payments/bsc/webhook")
async def bsc_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Custom BSCscan listener — same HMAC-SHA256 scheme as TronGrid.
    Header is `X-BSC-Signature`."""
    body = await request.body()
    sig = request.headers.get("x-bsc-signature", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        parsed = parse_bscscan(body, sig, payload or {})
    except WebhookError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ────────────── TON Wallet Pay (managed-pool routing) ───────────── #


@router.post("/payments/ton-pool/webhook")
async def ton_pool_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """TON Wallet Pay merchant webhook — separate path from the
    subscription billing one (`/payments/wallet-pay/*`) so the
    managed-pool routing is unambiguous in logs.

    Accepts either bearer (`Authorization`) or HMAC (`X-Wallet-Pay-Signature`)
    — operator can pin to one by leaving the other unset."""
    body = await request.body()
    auth = request.headers.get("authorization", "")
    sig = request.headers.get("x-wallet-pay-signature", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        parsed = parse_ton(auth, body, sig, payload or {})
    except WebhookError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)
