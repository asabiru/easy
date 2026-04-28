"""TON / Telegram Wallet payment endpoints.

  POST /payments/ton/invoice    — create a Wallet Pay invoice (KYC required)
  POST /payments/ton/webhook    — Wallet Pay callback (HMAC-validated)
  GET  /payments/me             — caller's payment history
  GET  /admin/payments          — all payments (admin/manager)

NO real funds-touching calls until both:
  * `TON_NETWORK=mainnet`
  * `TON_TREASURY_ADDRESS` set
  * KYC approved for the user

The `/invoice` endpoint refuses with 503 if mainnet not ready.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_role
from app.config.settings import get_settings
from app.database.models import AmlEvent, Payment, User
from app.database.session import get_db
from app.kyc.deps import require_kyc
from app.payments.ton import (
    is_mainnet_ready,
    make_invoice_payload,
    verify_wallet_pay_webhook,
)

log = logging.getLogger(__name__)
router = APIRouter()


class InvoiceCreate(BaseModel):
    amount_usdt: float = Field(..., gt=0, le=100_000)
    purpose: str = Field(default="subscription", pattern="^(subscription|vault_deposit|other)$")
    description: str = Field(default="SignalX top-up", max_length=128)


@router.post("/payments/ton/invoice")
def create_ton_invoice(
    body: InvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_kyc(enforce=True)),
) -> dict[str, Any]:
    """Create a Wallet Pay USDT-on-TON invoice. KYC required.

    For local dev / testnet, returns a stub invoice that bypasses the
    real Wallet Pay API — so flows can be exercised end-to-end without
    a merchant account.
    """
    if get_settings().app_env != "dev" and not is_mainnet_ready():
        raise HTTPException(
            status_code=503,
            detail="TON payments not configured (need TON_NETWORK=mainnet + TON_TREASURY_ADDRESS + TON_WALLET_PAY_API_KEY)",
        )

    try:
        wp_payload = make_invoice_payload(
            user_id=user.id,
            amount_usdt=body.amount_usdt,
            description=body.description,
        )
    except (RuntimeError, ValueError) as exc:
        # Dev mode without merchant key → still produce a stub invoice.
        if get_settings().app_env != "dev":
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        import secrets as _s
        wp_payload = {
            "externalId": f"dev-stub-{user.id}-{int(datetime.utcnow().timestamp())}-{_s.token_hex(4)}"
        }

    payment = Payment(
        user_id=user.id,
        provider="ton_wallet_pay",
        external_id=wp_payload["externalId"],
        amount_usdt=body.amount_usdt,
        purpose=body.purpose,
        status="pending",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    db.add(AmlEvent(
        user_id=user.id,
        actor_id=user.id,
        kind="payment_invoice_created",
        detail=json.dumps({
            "payment_id": payment.id,
            "amount_usdt": body.amount_usdt,
            "purpose": body.purpose,
            "external_id": payment.external_id,
        }),
    ))
    db.commit()

    return {
        "payment_id": payment.id,
        "external_id": payment.external_id,
        "amount_usdt": payment.amount_usdt,
        "purpose": payment.purpose,
        "pay_link": (
            f"https://pay.wallet.tg/checkout/{payment.external_id}"
            if is_mainnet_ready()
            else f"about:blank#dev-stub-{payment.external_id}"
        ),
        "expires_in_sec": 600,
    }


@router.post("/payments/ton/webhook")
async def ton_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Wallet Pay payment-status webhook.

    On `payment_received` events: marks the matching Payment row paid
    and writes an AML event. On `payment_failed`: marks failed.
    Idempotent on `external_id` — replayed events update at most once.
    """
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        verify_wallet_pay_webhook(headers, raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad JSON: {exc}") from exc

    external_id = payload.get("externalId") or payload.get("external_id")
    if not external_id:
        raise HTTPException(status_code=400, detail="missing externalId")
    event_type = (payload.get("type") or payload.get("status") or "").upper()

    payment = (
        db.query(Payment).filter(Payment.external_id == external_id).first()
    )
    if payment is None:
        return {"ok": True, "applied": False, "reason": "unknown external_id"}

    # Idempotency — don't downgrade a paid payment.
    if payment.status == "paid":
        return {"ok": True, "applied": False, "reason": "already paid"}

    if event_type in ("PAYMENT_RECEIVED", "PAID", "SUCCESS"):
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
    elif event_type in ("PAYMENT_FAILED", "FAILED", "EXPIRED"):
        payment.status = "failed"
    else:
        return {"ok": True, "applied": False, "reason": f"ignored event {event_type}"}

    db.add(payment)
    db.add(AmlEvent(
        user_id=payment.user_id,
        kind="payment_status_change",
        detail=json.dumps({
            "payment_id": payment.id,
            "external_id": external_id,
            "status": payment.status,
            "event_type": event_type,
        }),
    ))
    db.commit()
    return {"ok": True, "applied": True, "status": payment.status}


@router.get("/payments/me")
def my_payments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    rows = (
        db.query(Payment)
        .filter(Payment.user_id == user.id)
        .order_by(Payment.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": p.id,
            "external_id": p.external_id,
            "provider": p.provider,
            "amount_usdt": p.amount_usdt,
            "purpose": p.purpose,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in rows
    ]


@router.get("/admin/payments")
def admin_payments(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("manager", "admin")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = (
        db.query(Payment)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "external_id": p.external_id,
            "provider": p.provider,
            "amount_usdt": p.amount_usdt,
            "purpose": p.purpose,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in rows
    ]
