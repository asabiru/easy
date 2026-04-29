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

Security posture
----------------
* Per-IP sliding-window rate limit — default 60/min/IP. A legitimate
  provider (TronGrid / Alchemy / Helius / BSCscan / Wallet Pay)
  delivers at most a few hundred per day to a single endpoint;
  60/min is comfortable headroom but caps brute-force secret
  discovery to 60 guesses / minute / source IP.
* Signature failures emit a `custody_deposit_webhook_signature_invalid`
  audit-log row with the source IP + chain. Required for STRIDE
  monitoring — silent 401s would let an attacker probe the secret
  invisibly.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.custody.webhooks import (
    InboundDeposit,
    WebhookError,
    parse_alchemy,
    parse_bscscan,
    parse_helius,
    parse_ton,
    parse_trongrid,
    record_inbound_deposit,
)
from app.database.models import AmlEvent
from app.database.session import get_db
from app.security.rate_limit import RateLimiter

log = logging.getLogger(__name__)
router = APIRouter()

# Per-chain limiter: separate buckets so one chain's traffic doesn't
# starve another. 60/min/IP is well above any legitimate provider rate.
_rl_trongrid = RateLimiter("payments_trongrid_webhook", per_ip_per_min=60)
_rl_alchemy = RateLimiter("payments_alchemy_webhook", per_ip_per_min=60)
_rl_helius = RateLimiter("payments_helius_webhook", per_ip_per_min=60)
_rl_bsc = RateLimiter("payments_bsc_webhook", per_ip_per_min=60)
_rl_ton = RateLimiter("payments_ton_pool_webhook", per_ip_per_min=60)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _audit_signature_failure(
    db: Session, *, chain: str, ip: str, status_code: int, detail: str
) -> None:
    """Write an audit row for any 4xx that came out of signature
    verification. Failures must be visible — silent 401s let an
    attacker probe the secret without any signal to the operator."""
    try:
        db.add(AmlEvent(
            user_id=None,
            actor_id=None,
            kind="custody_deposit_webhook_signature_invalid",
            detail=json.dumps({
                "chain": chain,
                "ip": ip,
                "status": status_code,
                "reason": detail,
            }),
        ))
        db.commit()
    except Exception:  # pragma: no cover — never let audit IO break the route
        log.exception("failed to write signature-failure audit row")
        db.rollback()


def _to_response(result, parsed: InboundDeposit) -> dict[str, Any]:
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


async def _read_json(request: Request) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload


# ────────────────────────── TRC20 (TronGrid) ────────────────────── #


@router.post("/payments/trongrid/webhook")
async def trongrid_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_rl_trongrid),
) -> dict[str, Any]:
    """TronGrid signature: hex HMAC-SHA256 over raw body, header
    `X-TronGrid-Signature`."""
    body = await request.body()
    sig = request.headers.get("x-trongrid-signature", "")
    payload = await _read_json(request)
    try:
        parsed = parse_trongrid(body, sig, payload or {})
    except WebhookError as e:
        if e.status_code == 401:
            _audit_signature_failure(db, chain="trc20", ip=_client_ip(request),
                                     status_code=e.status_code, detail=e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ────────────────────────── ERC20 (Alchemy) ────────────────────── #


@router.post("/payments/alchemy/webhook")
async def alchemy_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_rl_alchemy),
) -> dict[str, Any]:
    """Alchemy `Address Activity`: header `X-Alchemy-Signature`, hex
    HMAC-SHA256 over raw body. Signing key set in Alchemy webhook UI."""
    body = await request.body()
    sig = request.headers.get("x-alchemy-signature", "")
    payload = await _read_json(request)
    try:
        parsed = parse_alchemy(body, sig, payload or {})
    except WebhookError as e:
        if e.status_code == 401:
            _audit_signature_failure(db, chain="erc20", ip=_client_ip(request),
                                     status_code=e.status_code, detail=e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ─────────────────────────── Solana (Helius) ────────────────────── #


@router.post("/payments/helius/webhook")
async def helius_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_rl_helius),
) -> dict[str, Any]:
    """Helius enhanced-webhook: bearer token in `Authorization` header."""
    auth = request.headers.get("authorization", "")
    payload = await _read_json(request)
    try:
        parsed = parse_helius(auth, payload or [])
    except WebhookError as e:
        if e.status_code == 401:
            _audit_signature_failure(db, chain="sol", ip=_client_ip(request),
                                     status_code=e.status_code, detail=e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ─────────────────────────── BSC (custom) ───────────────────────── #


@router.post("/payments/bsc/webhook")
async def bsc_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_rl_bsc),
) -> dict[str, Any]:
    """Custom BSCscan listener — same HMAC-SHA256 scheme as TronGrid.
    Header is `X-BSC-Signature`."""
    body = await request.body()
    sig = request.headers.get("x-bsc-signature", "")
    payload = await _read_json(request)
    try:
        parsed = parse_bscscan(body, sig, payload or {})
    except WebhookError as e:
        if e.status_code == 401:
            _audit_signature_failure(db, chain="bsc", ip=_client_ip(request),
                                     status_code=e.status_code, detail=e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)


# ────────────── TON Wallet Pay (managed-pool routing) ───────────── #


@router.post("/payments/ton-pool/webhook")
async def ton_pool_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_rl_ton),
) -> dict[str, Any]:
    """TON Wallet Pay merchant webhook — separate path from the
    subscription billing one (`/payments/wallet-pay/*`) so the
    managed-pool routing is unambiguous in logs.

    Accepts either bearer (`Authorization`) or HMAC (`X-Wallet-Pay-Signature`)
    — operator can pin to one by leaving the other unset."""
    body = await request.body()
    auth = request.headers.get("authorization", "")
    sig = request.headers.get("x-wallet-pay-signature", "")
    payload = await _read_json(request)
    try:
        parsed = parse_ton(auth, body, sig, payload or {})
    except WebhookError as e:
        if e.status_code == 401:
            _audit_signature_failure(db, chain="ton", ip=_client_ip(request),
                                     status_code=e.status_code, detail=e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    result = record_inbound_deposit(db, parsed)
    db.commit()
    return _to_response(result, parsed)
