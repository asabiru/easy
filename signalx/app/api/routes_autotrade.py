"""Auto-trade subscription endpoints.

Onboarding flow:

  POST /autotrade/subscribe
    → creates a paper-mode subscription, encrypts the API keys, returns id.

  POST /autotrade/{id}/go-live
    → flips `live_trading_enabled=True`. Refused if the global kill switch
      (`Settings.enable_autotrade`) is False — operator must enable that
      first.

  POST /autotrade/{id}/kill        → status="killed" (immediate stop)
  POST /autotrade/{id}/paper-mode  → status="paper"  (back to paper)
  POST /autotrade/{id}/resume      → status="paper"  (clears 'paused')
  GET  /autotrade/{id}/status      → balance, pnl_today, recent orders
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_current_user_optional
from app.autotrade.crypto import encrypt
from app.compliance.risk_ack import require_risk_ack
from app.autotrade.risk_guard import daily_pnl
from app.config.settings import get_settings
from app.database.models import AutoTradeOrder, AutoTradeSubscription, User
from app.database.session import get_db
from app.security.rate_limit import test_keys_limiter

router = APIRouter()

TIERS = ("manual_plus", "auto_lite", "auto_pro", "vip")


def _own_or_admin(db: Session, sub_id: int, user: User) -> AutoTradeSubscription:
    """Fetch subscription and assert the caller owns it (by user_id or email)
    or is an admin. Used by every mutation + status endpoint to prevent
    anonymous / cross-tenant access."""
    sub = _get_sub(db, sub_id)
    if user.role == "admin":
        return sub
    if sub.user_id is not None and sub.user_id == user.id:
        return sub
    if sub.user_id is None and (sub.email or "").lower() == (user.email or "").lower():
        return sub
    raise HTTPException(status_code=403, detail="not your subscription")


class SubscribeIn(BaseModel):
    email: EmailStr
    tier: Literal["manual_plus", "auto_lite", "auto_pro", "vip"]
    exchange_id: str = Field(..., examples=["bybit"])
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    api_passphrase: str | None = None
    max_position_pct: float | None = None
    daily_loss_limit_pct: float | None = None
    min_signal_score: int | None = None
    max_fake_risk: int | None = None
    allowed_symbols: list[str] | None = None


@router.post("/autotrade/subscribe")
def subscribe(
    payload: SubscribeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a paper-mode auto-trade subscription for the calling user.

    Authentication is required (CLAUDE.md Rule 3 + security SKILL.md A01):
    this endpoint stores encrypted exchange API credentials and ties them
    to a user identity, so an anonymous subscribe path would (a) leak
    paper-order signal data to whoever can spam the endpoint, and (b)
    leave orphaned subscription rows that can't be deleted via the
    user-scoped endpoints. Frontend signup flow logs the user in first
    (POST /auth/register → token → POST /autotrade/subscribe).

    Compliance: requires the caller to have accepted the current
    risk-disclosure version (POST /compliance/risk-ack). Admin/manager
    bypass — they're acting on behalf of clients via the dashboard.
    """
    require_risk_ack(user)
    s = get_settings()
    sub = AutoTradeSubscription(
        # Always store the local-part lowercased so that downstream
        # ownership checks against `user.email` (which routes_auth always
        # lowercases on registration) match in case-sensitive databases
        # like Postgres.
        email=str(payload.email).lower(),
        user_id=user.id,
        tier=payload.tier,
        exchange_id=payload.exchange_id.lower(),
        api_key_encrypted=encrypt(payload.api_key),
        api_secret_encrypted=encrypt(payload.api_secret),
        api_passphrase_encrypted=encrypt(payload.api_passphrase) if payload.api_passphrase else None,
        max_position_pct=payload.max_position_pct if payload.max_position_pct is not None else s.autotrade_default_max_position_pct,
        daily_loss_limit_pct=payload.daily_loss_limit_pct if payload.daily_loss_limit_pct is not None else s.autotrade_default_daily_loss_limit_pct,
        min_signal_score=payload.min_signal_score if payload.min_signal_score is not None else 60,
        max_fake_risk=payload.max_fake_risk if payload.max_fake_risk is not None else 49,
        allowed_symbols=json.dumps(payload.allowed_symbols) if payload.allowed_symbols else None,
        status="paper",
        live_trading_enabled=False,
        paper_until=datetime.utcnow() + timedelta(days=s.autotrade_default_paper_days),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {
        "subscription_id": sub.id,
        "tier": sub.tier,
        "status": sub.status,
        "paper_until": sub.paper_until.isoformat() if sub.paper_until else None,
        "live_trading_enabled": sub.live_trading_enabled,
    }


@router.post("/autotrade/{sub_id}/go-live")
def go_live(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    s = get_settings()
    sub = _own_or_admin(db, sub_id, user)
    # KYC gate: live trading dispatches real orders against the
    # subscription **owner's** exchange account, so the owner MUST be
    # KYC-approved — even when an admin is the one calling /go-live on
    # their behalf via the compliance dashboard. Earlier code looked up
    # `user.id` (the caller), which let an admin go-live a subscription
    # whose owner had failed KYC or had a sanctions hit.
    # (BUG_0003 in pr-review-job-ee8860e4a19c462b9a8c10edb7a3ec14.)
    #
    # When `kyc_required=False` (early MVP / mock provider), this is a
    # no-op so existing flows still work. We always check the owner if
    # `sub.user_id` is set; legacy un-owned subs (only `email` set) skip
    # the gate the same way they did before — those are exception paths
    # only created in tests.
    if s.kyc_required and sub.user_id is not None:
        from app.database.models import KycProfile  # local: avoid circular
        profile = (
            db.query(KycProfile)
            .filter(KycProfile.user_id == sub.user_id)
            .first()
        )
        if profile is None or profile.status != "approved":
            raise HTTPException(
                status_code=403,
                detail=(
                    "KYC verification required for the subscription owner "
                    "before live trading. POST /kyc/start."
                ),
            )
        if profile.sanctions_hit:
            raise HTTPException(
                status_code=403,
                detail="subscription owner is blocked by AML screening",
            )
    if not s.enable_autotrade:
        raise HTTPException(
            status_code=409,
            detail="global autotrade kill switch is off (ENABLE_AUTOTRADE=false)",
        )
    # 2FA gate for the higher tiers — VIP and Auto-Pro both have leverage
    # and notional that warrant a hardware-key class authn before any live
    # order dispatch. Mirrors the KYC gate above: we always check the
    # subscription **owner's** TOTP state, not the caller's. Otherwise an
    # admin calling /go-live on behalf of a client would skip the 2FA hard
    # rule documented in routes_2fa.py ("Mandatory tiers: VIP and Auto-Pro
    # must have totp_enabled=True before go-live"). For un-owned legacy
    # subs (`sub.user_id is None`, exception path created only in tests),
    # we fall back to the caller — same as the old behaviour.
    # (BUG_0003 in pr-review-job-982f38de35804aaeaed8f092e5a1cded.)
    if sub.tier in ("vip", "auto_pro"):
        if sub.user_id is not None:
            owner = db.query(User).filter(User.id == sub.user_id).first()
            owner_totp_enabled = bool(owner.totp_enabled) if owner else False
        else:
            owner_totp_enabled = bool(user.totp_enabled)
        if not owner_totp_enabled:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"2FA required for tier '{sub.tier}'. The subscription "
                    "owner must enable TOTP via POST /auth/2fa/setup before "
                    "live trading."
                ),
            )
    # Risk acknowledgement re-checked at go-live so a bumped
    # RISK_ACK_VERSION blocks live trading until re-accepted.
    require_risk_ack(user)
    # Killed/paused subscriptions must go through /resume → paper before
    # they can flip back to live. Otherwise the daily-loss-pause safety
    # mechanism (G4) is bypassable: a user whose sub was paused by the risk
    # guard could just re-enable live trading directly. (BUG_0001 in
    # pr-review-job-cec3c29938df4bfe902e2210e7446cf1.)
    if sub.status in ("killed", "paused"):
        raise HTTPException(
            status_code=409,
            detail=f"subscription is {sub.status}; call /resume first",
        )
    if sub.paper_until and sub.paper_until > datetime.utcnow():
        raise HTTPException(
            status_code=409,
            detail=f"subscription is in paper mode until {sub.paper_until.isoformat()}",
        )
    sub.status = "live"
    sub.live_trading_enabled = True
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": sub.status, "live_trading_enabled": True}


@router.post("/autotrade/{sub_id}/test-keys")
def test_keys(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _rl: None = Depends(test_keys_limiter),
) -> dict[str, Any]:
    """Health-check the stored API key against the exchange.

    Returns:
      {ok: bool, exchange: str, error: str|null, masked_key: str}

    Doesn't read or return raw key material — only first/last 4 chars
    of the API key id (not the secret) for visual confirmation in the UI.

    Implementation: ccxt's `fetch_balance()` is the cheapest authenticated
    call. We catch every ccxt exception class and return a structured
    error so the client can render a useful message instead of a 500.
    """
    from app.autotrade.crypto import decrypt

    sub = _own_or_admin(db, sub_id, user)
    if not sub.api_key_encrypted:
        return {"ok": False, "exchange": sub.exchange_id, "error": "no api key on file", "masked_key": ""}
    try:
        api_key = decrypt(sub.api_key_encrypted)
    except Exception as exc:  # decrypt failed (bad enc-key, corrupt blob)
        return {"ok": False, "exchange": sub.exchange_id, "error": f"decrypt error: {type(exc).__name__}", "masked_key": ""}
    masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) >= 8 else "…"

    # ccxt is heavy and not always present on minimal test installs; lazy
    # import so this endpoint doesn't break unrelated tests.
    try:
        import ccxt  # type: ignore
    except Exception:
        return {"ok": False, "exchange": sub.exchange_id, "error": "ccxt not installed", "masked_key": masked}

    try:
        klass = getattr(ccxt, sub.exchange_id)
    except AttributeError:
        return {"ok": False, "exchange": sub.exchange_id, "error": f"unknown exchange '{sub.exchange_id}'", "masked_key": masked}

    # Decrypt the secret + optional passphrase. ccxt's authenticated calls
    # (fetch_balance, fetch_positions, etc.) require both apiKey and secret;
    # OKX/KuCoin additionally need `password` (passphrase).
    try:
        api_secret = decrypt(sub.api_secret_encrypted) if sub.api_secret_encrypted else ""
        passphrase = (
            decrypt(sub.api_passphrase_encrypted) if sub.api_passphrase_encrypted else None
        )
    except Exception as exc:
        return {"ok": False, "exchange": sub.exchange_id, "error": f"decrypt error: {type(exc).__name__}", "masked_key": masked}

    config: dict[str, Any] = {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
    if passphrase:
        config["password"] = passphrase

    try:
        instance = klass(config)
        instance.fetch_balance()
        return {"ok": True, "exchange": sub.exchange_id, "error": None, "masked_key": masked}
    except Exception as exc:
        return {"ok": False, "exchange": sub.exchange_id, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "masked_key": masked}


@router.post("/autotrade/{sub_id}/kill")
def kill(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    sub.status = "killed"
    sub.live_trading_enabled = False
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "killed"}


@router.post("/autotrade/{sub_id}/paper-mode")
def paper_mode(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    # Same rationale as /go-live: paused/killed subs must go through
    # /resume so the daily-loss-pause + admin-kill workflows can't be
    # bypassed by switching directly to paper. The executor dispatches
    # to status in (paper, live), so flipping a paused sub to paper
    # here would silently re-enable order flow. (BUG_0001 in
    # pr-review-job-5f8d54f0bce5493e86a1b963275ea000.)
    if sub.status in ("paused", "killed"):
        raise HTTPException(
            status_code=409,
            detail=f"subscription is {sub.status}; call /resume first",
        )
    sub.status = "paper"
    sub.live_trading_enabled = False
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "paper"}


@router.post("/autotrade/{sub_id}/resume")
def resume(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    if sub.status not in ("paused", "killed"):
        raise HTTPException(status_code=409, detail=f"subscription is {sub.status}, nothing to resume")
    sub.status = "paper"
    sub.live_trading_enabled = False
    sub.last_paused_reason = None
    db.add(sub)
    db.commit()
    return {"subscription_id": sub.id, "status": "paper"}


@router.get("/autotrade/{sub_id}/status")
def status(
    sub_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    sub = _own_or_admin(db, sub_id, user)
    orders = (
        db.query(AutoTradeOrder)
        .filter(AutoTradeOrder.subscription_id == sub.id)
        .order_by(AutoTradeOrder.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "subscription_id": sub.id,
        "tier": sub.tier,
        "status": sub.status,
        "live_trading_enabled": sub.live_trading_enabled,
        "paper_until": sub.paper_until.isoformat() if sub.paper_until else None,
        "last_paused_reason": sub.last_paused_reason,
        "exchange_id": sub.exchange_id,
        "max_position_pct": sub.max_position_pct,
        "daily_loss_limit_pct": sub.daily_loss_limit_pct,
        "min_signal_score": sub.min_signal_score,
        "max_fake_risk": sub.max_fake_risk,
        "pnl_today": round(daily_pnl(orders), 2),
        "recent_orders": [
            {
                "id": o.id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "mode": o.mode,
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.qty,
                "entry_price": o.entry_price,
                "status": o.status,
                "rejected_reason": o.rejected_reason,
                "realized_pnl": o.realized_pnl,
            }
            for o in orders
        ],
    }


class PositionSizeIn(BaseModel):
    equity: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    stop_loss_price: float = Field(..., gt=0)
    side: Literal["long", "short"]
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=10)
    max_position_pct: float | None = Field(default=None, gt=0, le=100)
    use_daily_loss_cap: bool = True


@router.post("/autotrade/{sub_id}/position-size")
def position_size(
    sub_id: int,
    payload: PositionSizeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Compute the recommended position size given a subscription's
    risk caps and the caller's stop-loss intent.

    Pure-math endpoint — does not place any order. Surfaces which cap
    bound the size (risk budget vs notional cap vs remaining daily-loss
    budget) so the trader can see the constraint live in the /app UI.
    """
    from app.autotrade.position_sizing import calc

    sub = _own_or_admin(db, sub_id, user)
    # Unit-conversion gotcha: AutoTradeSubscription stores the caps as
    # **decimal fractions** (0.10 = 10%) — that's what risk_guard.py
    # expects (notional_cap = free_balance * max_position_pct, no /100).
    # `position_sizing.calc()` takes whole-number percentages (10 = 10%)
    # because that matches how the calculator UI presents them. Convert
    # here. Also follow the repo's hard "is not None" rule (security
    # SKILL.md + reviewer SKILL.md) so an explicit 0 doesn't silently
    # fall back to the default.
    if payload.max_position_pct is not None:
        max_position_pct = payload.max_position_pct
    elif sub.max_position_pct is not None:
        max_position_pct = sub.max_position_pct * 100  # decimal → whole pct
    else:
        max_position_pct = 25.0

    daily_remaining: float | None = None
    if payload.use_daily_loss_cap:
        cap_pct = (
            sub.daily_loss_limit_pct
            if sub.daily_loss_limit_pct is not None
            else 0.05
        )
        cap_abs = payload.equity * cap_pct  # cap_pct is already a fraction
        orders = (
            db.query(AutoTradeOrder)
            .filter(AutoTradeOrder.subscription_id == sub.id)
            .all()
        )
        used = max(0.0, -daily_pnl(orders))
        daily_remaining = max(0.0, cap_abs - used)

    return calc(
        equity=payload.equity,
        entry_price=payload.entry_price,
        stop_loss_price=payload.stop_loss_price,
        side=payload.side,
        risk_per_trade_pct=payload.risk_per_trade_pct,
        max_position_pct=max_position_pct,
        daily_loss_remaining=daily_remaining,
    )


def _get_sub(db: Session, sub_id: int) -> AutoTradeSubscription:
    sub = db.query(AutoTradeSubscription).filter(AutoTradeSubscription.id == sub_id).first()
    if sub is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    return sub
