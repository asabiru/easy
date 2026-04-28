"""Round-2 parallel agent contribution tests.

Covers Security 2FA, Compliance risk-ack gate, and Marketing lead
capture — the three new public-shape changes introduced in this round."""
from __future__ import annotations

import os

import pyotp


# ─────────────────────────── helpers ─────────────────────────── #

def _register(client, email: str = "user@example.com", password: str = "pw1234567"):
    """Register + log in. /auth/login sets a session cookie picked up by
    the TestClient cookie jar automatically; the returned token is also
    set on the Authorization header for endpoints that prefer Bearer."""
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    token = body.get("token") or body.get("access_token")
    if token:
        client.headers.update({"Authorization": f"Bearer {token}"})
    return body


# ─────────────────────────── Security 2FA ─────────────────────────── #

def test_2fa_setup_returns_provisioning_uri(client_with_db):
    cl, _ = client_with_db
    _register(cl, "tfa1@example.com")
    r = cl.post("/auth/2fa/setup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["secret"], str) and len(body["secret"]) >= 16
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert "issuer=SignalX" in body["provisioning_uri"]


def test_2fa_verify_with_correct_code_enables(client_with_db):
    cl, _ = client_with_db
    _register(cl, "tfa2@example.com")
    setup = cl.post("/auth/2fa/setup").json()
    code = pyotp.TOTP(setup["secret"]).now()
    r = cl.post("/auth/2fa/verify", json={"code": code})
    assert r.status_code == 200
    assert r.json()["totp_enabled"] is True

    # status endpoint should reflect it
    r = cl.get("/auth/2fa/status")
    assert r.status_code == 200
    assert r.json()["totp_enabled"] is True


def test_2fa_verify_rejects_wrong_code(client_with_db):
    cl, _ = client_with_db
    _register(cl, "tfa3@example.com")
    cl.post("/auth/2fa/setup")
    r = cl.post("/auth/2fa/verify", json={"code": "000000"})
    assert r.status_code == 400


def test_2fa_setup_409_after_enabled(client_with_db):
    cl, _ = client_with_db
    _register(cl, "tfa4@example.com")
    secret = cl.post("/auth/2fa/setup").json()["secret"]
    cl.post("/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()})
    r = cl.post("/auth/2fa/setup")
    assert r.status_code == 409


def test_2fa_disable_requires_valid_code(client_with_db):
    cl, _ = client_with_db
    _register(cl, "tfa5@example.com")
    secret = cl.post("/auth/2fa/setup").json()["secret"]
    cl.post("/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()})
    # wrong code → 400, still enabled
    r = cl.post("/auth/2fa/disable", json={"code": "000000"})
    assert r.status_code == 400
    assert cl.get("/auth/2fa/status").json()["totp_enabled"] is True
    # correct code → disabled
    r = cl.post("/auth/2fa/disable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    assert cl.get("/auth/2fa/status").json()["totp_enabled"] is False


# ─────────────────────────── Compliance risk-ack ─────────────────────────── #

def test_risk_ack_status_starts_false(client_with_db):
    cl, _ = client_with_db
    _register(cl, "ack1@example.com")
    r = cl.get("/compliance/risk-ack")
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    assert r.json()["current_version"] >= 1


def test_risk_ack_accept_records_version_and_timestamp(client_with_db):
    cl, _ = client_with_db
    _register(cl, "ack2@example.com")
    current = cl.get("/compliance/risk-ack").json()["current_version"]
    r = cl.post("/compliance/risk-ack", json={"version": current, "accepted": True})
    assert r.status_code == 200
    assert r.json()["accepted_version"] == current

    r = cl.get("/compliance/risk-ack")
    assert r.json()["accepted"] is True
    assert r.json()["accepted_at"] is not None


def test_risk_ack_rejects_version_mismatch(client_with_db):
    cl, _ = client_with_db
    _register(cl, "ack3@example.com")
    r = cl.post("/compliance/risk-ack", json={"version": 9999, "accepted": True})
    assert r.status_code == 409


def test_subscribe_412_when_gate_enabled_and_unack(client_with_db, monkeypatch):
    cl, _ = client_with_db
    monkeypatch.setenv("COMPLIANCE_RISK_ACK_REQUIRED", "true")
    _register(cl, "ack4@example.com")
    r = cl.post(
        "/autotrade/subscribe",
        json={
            "email": "ack4@example.com",
            "tier": "manual_plus",
            "exchange_id": "bybit",
            "api_key": "k" * 16,
            "api_secret": "s" * 16,
        },
    )
    assert r.status_code == 412


def test_subscribe_passes_after_ack(client_with_db, monkeypatch):
    cl, _ = client_with_db
    monkeypatch.setenv("COMPLIANCE_RISK_ACK_REQUIRED", "true")
    _register(cl, "ack5@example.com")
    current = cl.get("/compliance/risk-ack").json()["current_version"]
    cl.post("/compliance/risk-ack", json={"version": current, "accepted": True})
    r = cl.post(
        "/autotrade/subscribe",
        json={
            "email": "ack5@example.com",
            "tier": "manual_plus",
            "exchange_id": "bybit",
            "api_key": "k" * 16,
            "api_secret": "s" * 16,
        },
    )
    assert r.status_code == 200, r.text


# ─────────────────────────── Marketing leads ─────────────────────────── #

def test_lead_subscribe_creates_row(client_with_db):
    cl, db = client_with_db
    r = cl.post(
        "/leads/subscribe",
        json={
            "email": "Newsletter@Example.com",
            "utm_source": "twitter",
            "utm_medium": "social",
            "utm_campaign": "launch",
            "referral_code": "SXAB23CD",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is False
    assert body["email"] == "newsletter@example.com"

    from app.database.models import LeadCapture
    row = db.query(LeadCapture).filter(LeadCapture.id == body["id"]).first()
    assert row.utm_source == "twitter"
    assert row.referral_code == "SXAB23CD"


def test_lead_subscribe_idempotent(client_with_db):
    cl, _ = client_with_db
    cl.post("/leads/subscribe", json={"email": "dup@example.com"})
    r = cl.post("/leads/subscribe", json={"email": "DUP@Example.com"})
    assert r.status_code == 200
    assert r.json()["duplicate"] is True


def test_lead_subscribe_strips_bogus_referral_code(client_with_db):
    cl, db = client_with_db
    r = cl.post(
        "/leads/subscribe",
        json={"email": "junk@example.com", "referral_code": "<script>"},
    )
    assert r.status_code == 200
    from app.database.models import LeadCapture
    row = db.query(LeadCapture).filter(LeadCapture.email == "junk@example.com").first()
    assert row.referral_code is None


def test_lead_unsubscribe_does_not_leak(client_with_db):
    cl, _ = client_with_db
    # Unsubscribe an email that was never subscribed — must still be 200
    r = cl.post("/leads/unsubscribe", json={"email": "ghost@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ─────────────────────────── Position sizing ─────────────────────────── #

def test_position_sizing_pure_math_units():
    from app.autotrade.position_sizing import calc

    # 10000 equity, 1% risk = $100 risk budget
    # Long entry 100, stop 95 → distance 5 → qty = 100/5 = 20
    r = calc(
        equity=10000,
        entry_price=100,
        stop_loss_price=95,
        side="long",
        risk_per_trade_pct=1.0,
        max_position_pct=100,  # don't bind on notional
    )
    assert abs(r["qty"] - 20.0) < 1e-6
    assert r["binding_cap"] == "risk"
    assert abs(r["risk_amount"] - 100.0) < 1e-6


def test_position_sizing_max_position_binds():
    from app.autotrade.position_sizing import calc
    # 10000 equity, max_position_pct=10 → cap notional at $1000
    # Long entry 100 → max qty by notional = 10
    # 1% risk would have been 50 (5pt stop, 100 risk / 5 = 20)
    r = calc(
        equity=10000,
        entry_price=100,
        stop_loss_price=95,
        side="long",
        risk_per_trade_pct=2.0,  # would give qty 40 by risk alone
        max_position_pct=10,
    )
    assert r["binding_cap"] == "max_position"
    assert abs(r["qty"] - 10.0) < 1e-6


def test_position_sizing_daily_loss_binds():
    from app.autotrade.position_sizing import calc
    # Only $25 of daily-loss budget remaining; 5pt stop → max qty 5
    r = calc(
        equity=10000,
        entry_price=100,
        stop_loss_price=95,
        side="long",
        risk_per_trade_pct=2.0,
        max_position_pct=100,
        daily_loss_remaining=25,
    )
    assert r["binding_cap"] == "daily_loss"
    assert abs(r["qty"] - 5.0) < 1e-6


def test_position_sizing_zero_when_daily_budget_exhausted():
    from app.autotrade.position_sizing import calc
    r = calc(
        equity=10000, entry_price=100, stop_loss_price=95, side="long",
        daily_loss_remaining=0,
    )
    assert r["qty"] == 0.0
    assert r["binding_cap"] == "invalid"


def test_position_sizing_rejects_wrong_stop_side():
    from app.autotrade.position_sizing import calc
    # Long but stop ABOVE entry → invalid
    r = calc(
        equity=10000, entry_price=100, stop_loss_price=105, side="long",
    )
    assert r["binding_cap"] == "invalid"
    assert r["qty"] == 0


def test_position_sizing_endpoint_e2e(client_with_db):
    cl, _ = client_with_db
    _register(cl, "psize@example.com")
    r = cl.post(
        "/autotrade/subscribe",
        json={
            "email": "psize@example.com",
            "tier": "manual_plus",
            "exchange_id": "bybit",
            "api_key": "k" * 16,
            "api_secret": "s" * 16,
            "max_position_pct": 50,
            "daily_loss_limit_pct": 5,
        },
    )
    assert r.status_code == 200
    sub_id = r.json()["subscription_id"]

    r = cl.post(
        f"/autotrade/{sub_id}/position-size",
        json={
            "equity": 10000,
            "entry_price": 100,
            "stop_loss_price": 95,
            "side": "long",
            "risk_per_trade_pct": 1.0,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qty"] > 0
    assert body["binding_cap"] in {"risk", "max_position", "daily_loss"}


# ─────────────────────────── 2FA gate on go-live ─────────────────────────── #

def test_go_live_requires_2fa_for_vip(client_with_db, monkeypatch, request):
    """VIP and Auto-Pro tiers must have totp_enabled=True before go-live."""
    cl, db = client_with_db
    monkeypatch.setenv("ENABLE_AUTOTRADE", "true")
    monkeypatch.setenv("KYC_REQUIRED", "false")
    from app.config.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]
    # Reset the @lru_cache after the test so subsequent tests pick up
    # the reverted env vars instead of the cached overrides.
    request.addfinalizer(lambda: get_settings.cache_clear())  # type: ignore[attr-defined]

    _register(cl, "vip@example.com")
    r = cl.post(
        "/autotrade/subscribe",
        json={
            "email": "vip@example.com",
            "tier": "vip",
            "exchange_id": "bybit",
            "api_key": "k" * 16,
            "api_secret": "s" * 16,
        },
    )
    assert r.status_code == 200, r.text
    sub_id = r.json()["subscription_id"]

    # Force paper_until into the past so we don't hit the paper-mode gate.
    from datetime import datetime, timedelta
    from app.database.models import AutoTradeSubscription
    sub = db.query(AutoTradeSubscription).filter(AutoTradeSubscription.id == sub_id).first()
    sub.paper_until = datetime.utcnow() - timedelta(days=1)
    db.add(sub)
    db.commit()

    r = cl.post(f"/autotrade/{sub_id}/go-live")
    assert r.status_code == 403
    assert "2FA" in r.json()["detail"]
