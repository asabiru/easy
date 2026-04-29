"""TON / Wallet Pay endpoints + KYC gate + webhook signature."""
from __future__ import annotations

import hashlib
import hmac
import json
import os


def _register(client, email, password="hunter2-pass"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def _approve_kyc(client, token: str) -> str:
    started = client.post("/kyc/start", headers=_auth(token)).json()
    client.post("/kyc/webhook", json={
        "applicantId": started["applicant_id"],
        "result": "approved",
        "eventId": f"evt-{started['applicant_id']}",
    })
    return started["applicant_id"]


def test_invoice_requires_kyc(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-no-kyc@example.com")
    r = client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 50, "purpose": "subscription"},
    )
    # require_kyc returns 403 when status != approved.
    assert r.status_code == 403
    get_settings.cache_clear()


def test_invoice_succeeds_after_kyc_in_dev_mode(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-with-kyc@example.com")
    _approve_kyc(client, me["token"])

    r = client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 25.50, "purpose": "subscription", "description": "Auto-Pro monthly"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_usdt"] == 25.50
    assert body["purpose"] == "subscription"
    assert body["external_id"].startswith(("signalx-", "dev-stub-"))
    assert "pay_link" in body

    get_settings.cache_clear()


def test_invoice_rejected_in_prod_without_config(client, monkeypatch):
    """Outside dev, invoice creation fails until TON_NETWORK=mainnet etc."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TON_NETWORK", "testnet")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-prod-noconfig@example.com")
    _approve_kyc(client, me["token"])
    r = client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 10},
    )
    assert r.status_code == 503
    assert "TON" in r.json()["detail"]

    get_settings.cache_clear()


def test_my_payments_returns_user_history(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-history@example.com")
    _approve_kyc(client, me["token"])
    client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 99, "purpose": "subscription"},
    )
    client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 199, "purpose": "vault_deposit"},
    )

    r = client.get("/payments/me", headers=_auth(me["token"]))
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {r["amount_usdt"] for r in rows} == {99, 199}
    assert all(r["status"] == "pending" for r in rows)
    get_settings.cache_clear()


def test_webhook_marks_payment_paid_in_dev_mode(client, monkeypatch):
    """Dev mode skips signature check, so the webhook applies cleanly."""
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-webhook@example.com")
    _approve_kyc(client, me["token"])
    inv = client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 49.0, "purpose": "subscription"},
    ).json()

    r = client.post("/payments/ton/webhook", json={
        "externalId": inv["external_id"],
        "type": "PAYMENT_RECEIVED",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["status"] == "paid"

    rows = client.get("/payments/me", headers=_auth(me["token"])).json()
    assert rows[0]["status"] == "paid"
    assert rows[0]["paid_at"] is not None

    get_settings.cache_clear()


def test_webhook_idempotent_on_replay(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-idem@example.com")
    _approve_kyc(client, me["token"])
    inv = client.post(
        "/payments/ton/invoice",
        headers=_auth(me["token"]),
        json={"amount_usdt": 10, "purpose": "subscription"},
    ).json()

    payload = {"externalId": inv["external_id"], "type": "PAYMENT_RECEIVED"}
    r1 = client.post("/payments/ton/webhook", json=payload)
    r2 = client.post("/payments/ton/webhook", json=payload)
    assert r1.json()["applied"] is True
    assert r2.json()["applied"] is False  # second call short-circuits

    get_settings.cache_clear()


def test_webhook_unknown_external_id_does_not_500(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    r = client.post("/payments/ton/webhook", json={
        "externalId": "does-not-exist",
        "type": "PAYMENT_RECEIVED",
    })
    assert r.status_code == 200
    assert r.json()["applied"] is False
    get_settings.cache_clear()


def test_webhook_signature_required_in_prod(client, monkeypatch):
    """Prod webhooks without a valid HMAC must 401."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TON_WALLET_PAY_WEBHOOK_SECRET", "supersecret-prod")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    r = client.post("/payments/ton/webhook", json={
        "externalId": "anything",
        "type": "PAYMENT_RECEIVED",
    })
    assert r.status_code == 401

    # With correct signature it does not 401 (will 200 with applied=False
    # since the external_id is unknown).
    raw = json.dumps({"externalId": "anything", "type": "PAYMENT_RECEIVED"}).encode()
    sig = hmac.new(b"supersecret-prod", raw, hashlib.sha256).hexdigest()
    r2 = client.post(
        "/payments/ton/webhook",
        content=raw,
        headers={"content-type": "application/json", "walletpay-signature": sig},
    )
    assert r2.status_code == 200
    get_settings.cache_clear()


def test_admin_payments_requires_role(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "pay-admin-attempt@example.com")
    r = client.get("/admin/payments", headers=_auth(me["token"]))
    assert r.status_code == 403
    get_settings.cache_clear()
