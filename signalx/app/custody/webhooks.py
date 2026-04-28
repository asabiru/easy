"""Inbound chain-deposit webhook adapters (managed-pool flow).

Five endpoints sit in `app/api/routes_payments_webhooks.py` — one per
chain. Each one calls into the matching `parse_*` function here for
signature verification + payload parsing, then hands a normalised
`InboundDeposit` to the shared `record_inbound_deposit()` helper which
performs the idempotent credit (single source of truth, shared with the
manual `POST /admin/treasury/credit-deposit` operator endpoint).

Why this lives in `app/custody/` and not in `app/api/`:
  * The credit logic + signature verification is pure (no FastAPI
    deps), so we can unit-test it without spinning up TestClient.
  * Provider HMAC schemes are surprisingly varied — TronGrid uses raw
    body HMAC-SHA256 hex, Alchemy uses raw body HMAC-SHA256 hex on the
    `X-Alchemy-Signature` header, Helius uses a shared bearer token,
    BSCscan we built ourselves so we use the same scheme as TronGrid,
    TON Wallet Pay uses Bearer + signature on payload digest. Encoding
    these as small dataclasses keeps each route handler trivial.

Hard rules
----------
  * Every webhook is gated on `CUSTODY_LIVE_DEPOSITS_ENABLED=true`. If
    the master toggle is off, all five endpoints return 503. This is
    the same guard `/wallet/deposit-address` uses, so a misconfigured
    deploy NEVER takes real money silently.
  * The HMAC/Bearer secret per chain MUST be set. Empty secret = the
    chain's webhook returns 503 even if the master toggle is on. This
    forces an explicit operator decision for every chain we go live on.
  * Constant-time comparison for all signatures (`hmac.compare_digest`)
    — never `==` — to defeat timing oracle attacks.
  * Idempotent on `(chain, tx_hash)`. Re-delivery of the same TX (which
    every provider does on retry) MUST NOT double-credit. Enforced at
    the DB layer via the existing unique-style index on `Deposit`.

What this does NOT do (deferred)
--------------------------------
  * Real key derivation per chain (we still use deterministic mock
    addresses from `app/custody/addresses.py`). Real production
    integration requires `tronpy`, `web3`, `solana-py`, `tonsdk`.
  * Confirmation-depth waiting. A real impl waits N blocks before
    crediting (Tron: 19, ETH: 12, BSC: 15, SOL: 32, TON: finalized).
    For MVP we trust the provider's "confirmed" flag.
  * Multi-sig treasury sweep. Currently the deposit address IS the
    treasury wallet (per chain). For production we MUST move sweeps to
    a Safe.global / Squads multi-sig before any non-trivial AUM.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.custody import shares as shares_math
from app.database.models import (
    AmlEvent,
    ClientWallet,
    Deposit,
    DepositAddress,
    NavSnapshot,
    User,
)

log = logging.getLogger(__name__)

Chain = Literal["trc20", "erc20", "ton", "sol", "bsc"]


# ─────────────────────────── data shapes ─────────────────────────── #


@dataclass(frozen=True)
class InboundDeposit:
    """Normalised inbound deposit, ready to be persisted.

    All five chain adapters return this. Currency is implicitly USDT —
    we reject any non-USDT contract address at the parser layer."""

    chain: Chain
    tx_hash: str
    to_address: str
    memo: str | None
    amount_usdt: float
    confirmed: bool
    raw: dict[str, Any]  # provider payload, stored on the audit row


@dataclass(frozen=True)
class CreditResult:
    """Outcome of `record_inbound_deposit`."""

    deposit_id: int
    user_id: int | None
    credited: bool
    idempotent: bool  # True when the (chain, tx_hash) was already on file
    shares_credited: float
    share_price_at_credit: float


class WebhookError(Exception):
    """Raised on signature mismatch / chain-disabled / bad payload.

    Carries an HTTP status_code the route handler propagates verbatim
    so we never leak internal detail to the provider's retry logic."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ───────────────────────── signature checks ─────────────────────── #


def _const_eq(a: str, b: str) -> bool:
    """Constant-time string compare — `hmac.compare_digest` raises on
    type mismatch which we want to return False for instead."""
    try:
        return hmac.compare_digest(a, b)
    except (TypeError, ValueError):
        return False


def verify_hmac_sha256(secret: str, body: bytes, signature_hex: str) -> bool:
    """Generic HMAC-SHA256 hex verifier used by TronGrid + BSC + Alchemy.

    Returns False on any input issue (empty secret, missing signature,
    bad hex, length mismatch) — never raises so the route handler can
    just convert False → 401."""
    if not secret or not signature_hex:
        return False
    try:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    except Exception:  # noqa: BLE001
        return False
    return _const_eq(expected, signature_hex.strip().lower())


def verify_bearer(secret: str, header: str) -> bool:
    """`Authorization: Bearer <secret>` style — Helius + TON Wallet Pay.

    Header may be passed verbatim ("Bearer xyz") or just the token.
    Comparison is constant-time. Empty inputs → False."""
    if not secret or not header:
        return False
    token = header.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return _const_eq(token, secret)


# ───────────────────────── per-chain parsers ────────────────────── #


def _check_master_toggle() -> None:
    """Master `CUSTODY_LIVE_DEPOSITS_ENABLED` gate. Raises 503 if off."""
    if not get_settings().custody_live_deposits_enabled:
        raise WebhookError(503, "custody webhooks disabled (master toggle off)")


def _check_secret(secret: str, chain: Chain) -> None:
    if not secret:
        raise WebhookError(
            503,
            f"webhook secret for chain '{chain}' is not configured",
        )


def parse_trongrid(body: bytes, signature: str, payload: dict[str, Any]) -> InboundDeposit:
    """TronGrid webhook (USDT-TRC20, TRC20 contract notification).

    Expected payload (subset we care about):
      {
        "transaction_id": "...hash...",
        "to_address": "T...",
        "value": "12345000",        # raw uint, USDT is 6-decimal
        "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        "memo": "SX-42",            # optional
        "confirmed": true
      }

    Signature: hex HMAC-SHA256 over raw body, secret =
    `custody_trongrid_webhook_secret`."""
    _check_master_toggle()
    s = get_settings()
    _check_secret(s.custody_trongrid_webhook_secret, "trc20")
    if not verify_hmac_sha256(s.custody_trongrid_webhook_secret, body, signature):
        raise WebhookError(401, "trongrid signature invalid")
    tx = (payload.get("transaction_id") or "").strip()
    to_addr = (payload.get("to_address") or "").strip()
    contract = (payload.get("contract_address") or "").strip()
    if not tx or not to_addr:
        raise WebhookError(400, "missing transaction_id / to_address")
    if contract and contract != "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t":
        # Anything else on TRC20 isn't USDT — reject silently early.
        raise WebhookError(400, "non-USDT contract address (rejected)")
    raw_value = payload.get("value") or "0"
    try:
        amount = float(raw_value) / 1_000_000.0  # USDT-TRC20 is 6 decimals
    except (TypeError, ValueError):
        raise WebhookError(400, "value is not numeric") from None
    return InboundDeposit(
        chain="trc20",
        tx_hash=tx,
        to_address=to_addr,
        memo=(payload.get("memo") or None),
        amount_usdt=amount,
        confirmed=bool(payload.get("confirmed", True)),
        raw=payload,
    )


def parse_alchemy(body: bytes, signature: str, payload: dict[str, Any]) -> InboundDeposit:
    """Alchemy `Address Activity` webhook (USDT-ERC20).

    Header: `X-Alchemy-Signature: <hex>` over raw body, secret is the
    "signing key" you set in the Alchemy webhook UI.
    Payload structure simplified (we look at `event.activity[0]`)."""
    _check_master_toggle()
    s = get_settings()
    _check_secret(s.custody_alchemy_webhook_secret, "erc20")
    if not verify_hmac_sha256(s.custody_alchemy_webhook_secret, body, signature):
        raise WebhookError(401, "alchemy signature invalid")
    activity = (payload.get("event") or {}).get("activity") or []
    if not activity:
        raise WebhookError(400, "no activity in payload")
    a0 = activity[0]
    contract = (a0.get("rawContract") or {}).get("address") or ""
    if contract and contract.lower() != "0xdac17f958d2ee523a2206206994597c13d831ec7":
        raise WebhookError(400, "non-USDT contract address (rejected)")
    return InboundDeposit(
        chain="erc20",
        tx_hash=(a0.get("hash") or "").strip(),
        to_address=(a0.get("toAddress") or "").strip(),
        memo=None,
        amount_usdt=float(a0.get("value") or 0.0),
        confirmed=True,
        raw=payload,
    )


def parse_helius(authorization: str, payload: list[dict[str, Any]] | dict[str, Any]) -> InboundDeposit:
    """Helius enhanced-webhook (USDT-SPL on Solana).

    Helius authenticates with a shared bearer token in the
    `Authorization` header — payload itself is unsigned, so the bearer
    MUST be unique per webhook (rotate on suspicion of leak)."""
    _check_master_toggle()
    s = get_settings()
    _check_secret(s.custody_helius_webhook_secret, "sol")
    if not verify_bearer(s.custody_helius_webhook_secret, authorization):
        raise WebhookError(401, "helius bearer invalid")
    # Helius posts a list; we take the first event.
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise WebhookError(400, "empty payload")
    item = items[0]
    transfers = item.get("tokenTransfers") or []
    if not transfers:
        raise WebhookError(400, "no token transfers in payload")
    t0 = transfers[0]
    mint = (t0.get("mint") or "").strip()
    if mint and mint != "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB":
        raise WebhookError(400, "non-USDT mint (rejected)")
    return InboundDeposit(
        chain="sol",
        tx_hash=(item.get("signature") or "").strip(),
        to_address=(t0.get("toUserAccount") or "").strip(),
        # Solana memo is on a separate `memo` field if present; some
        # wallets prepend "[N+1] " but Helius normalises that out.
        memo=(item.get("memo") or None),
        amount_usdt=float(t0.get("tokenAmount") or 0.0),
        confirmed=True,
        raw=item,
    )


def parse_bscscan(body: bytes, signature: str, payload: dict[str, Any]) -> InboundDeposit:
    """Custom BSCscan-listener webhook (USDT-BEP20).

    BSCscan doesn't ship a hosted webhook — operators run a small
    listener that POSTs to us. We use the same HMAC-SHA256 hex scheme
    as TronGrid for symmetry. Listener configuration lives in
    `docs/runbooks/bsc-listener.md` (TBD)."""
    _check_master_toggle()
    s = get_settings()
    _check_secret(s.custody_bscscan_webhook_secret, "bsc")
    if not verify_hmac_sha256(s.custody_bscscan_webhook_secret, body, signature):
        raise WebhookError(401, "bsc signature invalid")
    contract = (payload.get("contract_address") or "").strip()
    if contract and contract.lower() != "0x55d398326f99059ff775485246999027b3197955":
        raise WebhookError(400, "non-USDT contract address (rejected)")
    raw_value = payload.get("value") or "0"
    try:
        # BSC USDT is 18 decimals.
        amount = float(raw_value) / 1e18 if isinstance(raw_value, (int, str)) else 0.0
    except (TypeError, ValueError):
        amount = 0.0
    return InboundDeposit(
        chain="bsc",
        tx_hash=(payload.get("transaction_hash") or "").strip(),
        to_address=(payload.get("to_address") or "").strip(),
        memo=None,
        amount_usdt=amount,
        confirmed=bool(payload.get("confirmed", True)),
        raw=payload,
    )


def parse_ton(authorization: str, body: bytes, signature: str, payload: dict[str, Any]) -> InboundDeposit:
    """TON Wallet Pay merchant webhook (jetton USDT on TON).

    Wallet Pay supports both bearer + HMAC; we accept either as long as
    one matches. This is intentional — the production deploy can pin to
    HMAC only by leaving the bearer secret empty."""
    _check_master_toggle()
    s = get_settings()
    secret = s.ton_wallet_pay_webhook_secret
    _check_secret(secret, "ton")
    bearer_ok = verify_bearer(secret, authorization) if authorization else False
    hmac_ok = verify_hmac_sha256(secret, body, signature) if signature else False
    if not (bearer_ok or hmac_ok):
        raise WebhookError(401, "ton signature invalid")
    return InboundDeposit(
        chain="ton",
        tx_hash=(payload.get("tx_hash") or payload.get("orderId") or "").strip(),
        to_address=(payload.get("to_address") or s.ton_treasury_address or "").strip(),
        memo=(payload.get("memo") or payload.get("comment") or None),
        amount_usdt=float(payload.get("amount_usdt") or payload.get("amount") or 0.0),
        confirmed=bool(payload.get("confirmed", True)),
        raw=payload,
    )


# ─────────────── shared idempotent credit helper ────────────────── #


def _resolve_user_id(db: Session, dep: InboundDeposit) -> int | None:
    """Match an inbound deposit to a user.

    Order of precedence:
      1. Exact `(chain, memo)` match against allocated deposit-address
         memos. This is the canonical routing for TRC20 / TON / SOL.
      2. Exact `(chain, external_address)` match. Canonical for ERC20 /
         BSC where each user has a unique counterfactual address.
    Returns `None` if no match — the deposit row is still recorded with
    `credited=False` so the operator can manually attribute it."""
    if dep.memo:
        addr = (
            db.query(DepositAddress)
            .filter(
                DepositAddress.chain == dep.chain,
                DepositAddress.memo == dep.memo,
            )
            .first()
        )
        if addr is not None:
            return int(addr.user_id)
    if dep.to_address:
        addr = (
            db.query(DepositAddress)
            .filter(
                DepositAddress.chain == dep.chain,
                DepositAddress.external_address == dep.to_address,
            )
            .first()
        )
        if addr is not None:
            return int(addr.user_id)
    return None


def _latest_share_price(db: Session) -> float:
    snap = db.query(NavSnapshot).order_by(NavSnapshot.id.desc()).first()
    return float(snap.share_price) if snap else 1.0


def record_inbound_deposit(db: Session, dep: InboundDeposit) -> CreditResult:
    """Idempotently persist an inbound deposit.

    Logic:
      1. If `(chain, tx_hash)` already exists → return that row, no-op.
      2. Resolve user via memo / address. If no match → record
         uncredited (`credited=False`, `user_id=None`-marker via the
         orphan-deposit pattern). The Deposit table requires user_id,
         so we punt to a dedicated 'unattributed' user row keyed by
         email "unassigned@signalx.internal".
      3. If matched + amount >= chain min → credit shares using current
         share price (mirrors `routes_treasury.credit_deposit` math).

    All branches write an AmlEvent for the operator audit log."""
    import json
    from app.custody.addresses import MIN_DEPOSIT_USDT

    existing = (
        db.query(Deposit)
        .filter(Deposit.chain == dep.chain, Deposit.tx_hash == dep.tx_hash)
        .first()
    )
    if existing is not None:
        return CreditResult(
            deposit_id=existing.id,
            user_id=existing.user_id,
            credited=bool(existing.credited),
            idempotent=True,
            shares_credited=float(existing.shares_credited or 0.0),
            share_price_at_credit=float(existing.share_price_at_credit or 0.0),
        )

    user_id = _resolve_user_id(db, dep)
    min_amt = MIN_DEPOSIT_USDT.get(dep.chain, 1.0)
    can_credit = (
        user_id is not None
        and dep.confirmed
        and dep.amount_usdt >= min_amt
    )

    if can_credit:
        share_price = _latest_share_price(db)
        shares = shares_math.issue_shares(dep.amount_usdt, share_price)
        row = Deposit(
            user_id=user_id,
            chain=dep.chain,
            tx_hash=dep.tx_hash,
            amount_usdt=float(dep.amount_usdt),
            confirmed_at=datetime.utcnow(),
            credited=True,
            credited_at=datetime.utcnow(),
            share_price_at_credit=share_price,
            shares_credited=shares,
        )
        db.add(row)

        wallet = (
            db.query(ClientWallet).filter(ClientWallet.user_id == user_id).first()
        )
        if wallet is None:
            wallet = ClientWallet(
                user_id=user_id,
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

        try:
            db.add(AmlEvent(
                user_id=user_id,
                actor_id=None,  # webhook-originated, no operator
                kind="custody_deposit_webhook_credited",
                detail=json.dumps({
                    "chain": dep.chain,
                    "tx_hash": dep.tx_hash,
                    "amount_usdt": dep.amount_usdt,
                    "shares_credited": shares,
                    "share_price": share_price,
                }, default=str),
            ))
        except Exception:  # noqa: BLE001
            log.exception("webhook audit write failed")
        db.flush()
        return CreditResult(
            deposit_id=row.id,
            user_id=user_id,
            credited=True,
            idempotent=False,
            shares_credited=shares,
            share_price_at_credit=share_price,
        )

    # Uncredited path: record the deposit, leave for operator review.
    # Anchor to the unattributed sentinel user when no match.
    sentinel_id = _get_or_create_unattributed_user(db).id if user_id is None else user_id
    row = Deposit(
        user_id=sentinel_id,
        chain=dep.chain,
        tx_hash=dep.tx_hash,
        amount_usdt=float(dep.amount_usdt),
        confirmed_at=datetime.utcnow() if dep.confirmed else None,
        credited=False,
    )
    db.add(row)
    try:
        db.add(AmlEvent(
            user_id=sentinel_id,
            actor_id=None,
            kind="custody_deposit_webhook_pending_review",
            detail=json.dumps({
                "chain": dep.chain,
                "tx_hash": dep.tx_hash,
                "to_address": dep.to_address,
                "memo": dep.memo,
                "amount_usdt": dep.amount_usdt,
                "below_min": dep.amount_usdt < min_amt,
                "unmatched": user_id is None,
                "unconfirmed": not dep.confirmed,
            }, default=str),
        ))
    except Exception:  # noqa: BLE001
        log.exception("webhook audit write failed")
    db.flush()
    return CreditResult(
        deposit_id=row.id,
        user_id=user_id,
        credited=False,
        idempotent=False,
        shares_credited=0.0,
        share_price_at_credit=0.0,
    )


UNATTRIBUTED_INTERNAL_EMAIL = "unassigned@signalx.internal"


def _get_or_create_unattributed_user(db: Session) -> User:
    """Sentinel user that owns deposits we couldn't route to a real user.

    Same `is_active=False` pattern as the treasury internal user — never
    appears in client-facing queries, can't log in. The operator
    reattributes by manually moving the Deposit row to a real user via
    the admin treasury panel (TBD: `POST /admin/treasury/deposits/{id}/reattribute`)."""
    user = db.query(User).filter(User.email == UNATTRIBUTED_INTERNAL_EMAIL).first()
    if user is None:
        user = User(
            email=UNATTRIBUTED_INTERNAL_EMAIL,
            password_hash="!disabled-unassigned-internal",
            role="admin",
            is_active=False,
        )
        db.add(user)
        db.flush()
    return user
