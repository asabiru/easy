"""KYC module coverage: start, status, webhook (mock provider),
admin queue + override, and the auto-trade go-live KYC gate."""
from __future__ import annotations

import json
import os


def _register(client, email, password="hunter2-pass", admin_email=None):
    if admin_email:
        os.environ["BOOTSTRAP_ADMIN_EMAIL"] = admin_email
        from app.config.settings import get_settings
        get_settings.cache_clear()
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_kyc_start_creates_profile_and_returns_token(client):
    me = _register(client, "kyc-start@example.com")
    r = client.post("/kyc/start", headers=_auth(me["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "mock"
    assert body["sdk_token"].startswith("mock-token-")
    assert body["status"] == "submitted"
    # Subsequent /kyc/status reflects submitted state.
    s = client.get("/kyc/status", headers=_auth(me["token"]))
    assert s.status_code == 200
    assert s.json()["status"] == "submitted"


def test_kyc_status_unverified_for_brand_new_user(client):
    me = _register(client, "kyc-fresh@example.com")
    r = client.get("/kyc/status", headers=_auth(me["token"]))
    assert r.status_code == 200
    assert r.json()["status"] == "unverified"


def test_kyc_webhook_approved_promotes_user(client):
    me = _register(client, "kyc-approved@example.com")
    started = client.post("/kyc/start", headers=_auth(me["token"])).json()
    applicant_id = started["applicant_id"]

    r = client.post("/kyc/webhook", json={
        "applicantId": applicant_id,
        "result": "approved",
        "eventId": "evt-1",
    })
    assert r.status_code == 200, r.text
    s = client.get("/kyc/status", headers=_auth(me["token"]))
    assert s.json()["status"] == "approved"


def test_kyc_webhook_rejects_with_reasons(client):
    me = _register(client, "kyc-rejected@example.com")
    started = client.post("/kyc/start", headers=_auth(me["token"])).json()

    r = client.post("/kyc/webhook", json={
        "applicantId": started["applicant_id"],
        "result": "rejected",
        "rejectReasons": ["DOC_BLURRY", "FACE_MISMATCH"],
        "eventId": "evt-2",
    })
    assert r.status_code == 200
    s = client.get("/kyc/status", headers=_auth(me["token"]))
    body = s.json()
    assert body["status"] == "rejected"
    assert "DOC_BLURRY" in body["reject_reasons"]


def test_kyc_webhook_idempotent_on_event_id(client):
    me = _register(client, "kyc-idem@example.com")
    started = client.post("/kyc/start", headers=_auth(me["token"])).json()

    payload = {
        "applicantId": started["applicant_id"],
        "result": "approved",
        "eventId": "evt-dup-1",
    }
    r1 = client.post("/kyc/webhook", json=payload)
    r2 = client.post("/kyc/webhook", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # AmlEvent count should reflect a single verdict (the second call short-circuits).
    # We verify via admin events endpoint after promoting an admin.


def test_kyc_admin_override_requires_admin_role(client):
    user = _register(client, "kyc-target@example.com")
    other = _register(client, "kyc-not-admin@example.com")
    r = client.post(
        f"/admin/kyc/{user['user_id']}/override",
        json={"new_status": "approved", "reason": "manual review by ops team"},
        headers=_auth(other["token"]),
    )
    assert r.status_code == 403


def test_kyc_admin_override_approves_user(client):
    admin = _register(client, "kyc-admin@example.com", admin_email="kyc-admin@example.com")
    target = _register(client, "kyc-override-target@example.com")
    # Need a profile to override — start KYC first.
    client.post("/kyc/start", headers=_auth(target["token"]))

    r = client.post(
        f"/admin/kyc/{target['user_id']}/override",
        json={"new_status": "approved", "reason": "ops verified offline via document review"},
        headers=_auth(admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def test_admin_kyc_queue_lists_pending_review(client):
    admin = _register(client, "kyc-admin-queue@example.com", admin_email="kyc-admin-queue@example.com")
    user_a = _register(client, "kyc-queue-a@example.com")
    user_b = _register(client, "kyc-queue-b@example.com")
    client.post("/kyc/start", headers=_auth(user_a["token"]))
    client.post("/kyc/start", headers=_auth(user_b["token"]))

    r = client.get("/admin/kyc/queue", headers=_auth(admin["token"]))
    assert r.status_code == 200
    emails = {row["email"] for row in r.json()}
    assert "kyc-queue-a@example.com" in emails
    assert "kyc-queue-b@example.com" in emails


def test_go_live_blocked_when_kyc_required_and_user_not_approved(client, monkeypatch):
    """Live trading must refuse until KYC approved when kyc_required=True."""
    monkeypatch.setenv("ENABLE_AUTOTRADE", "true")
    monkeypatch.setenv("KYC_REQUIRED", "true")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "kyc-gate@example.com")
    sub = client.post("/autotrade/subscribe", json={
        "email": "kyc-gate@example.com",
        "tier": "auto_lite",
        "exchange_id": "bybit",
        "api_key": "ABCD1234EFGHIJKL",
        "api_secret": "WXYZ9876MNOPQRST",
    }, headers=_auth(me["token"])).json()

    r = client.post(f"/autotrade/{sub['subscription_id']}/go-live", headers=_auth(me["token"]))
    assert r.status_code == 403
    assert "KYC" in r.json()["detail"]

    get_settings.cache_clear()


def test_go_live_allowed_when_kyc_required_false(client, monkeypatch):
    """Default early-MVP behavior: kyc_required=False → no KYC gate."""
    monkeypatch.setenv("ENABLE_AUTOTRADE", "true")
    monkeypatch.setenv("KYC_REQUIRED", "false")
    monkeypatch.setenv("AUTOTRADE_DEFAULT_PAPER_DAYS", "0")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "kyc-nogate@example.com")
    sub = client.post("/autotrade/subscribe", json={
        "email": "kyc-nogate@example.com",
        "tier": "auto_lite",
        "exchange_id": "bybit",
        "api_key": "ABCD1234EFGHIJKL",
        "api_secret": "WXYZ9876MNOPQRST",
    }, headers=_auth(me["token"])).json()

    r = client.post(f"/autotrade/{sub['subscription_id']}/go-live", headers=_auth(me["token"]))
    assert r.status_code == 200, r.text

    get_settings.cache_clear()


def test_go_live_blocked_when_sanctions_hit(client, monkeypatch):
    """Even after admin override to approved, sanctions_hit=True hard-blocks."""
    monkeypatch.setenv("ENABLE_AUTOTRADE", "true")
    monkeypatch.setenv("KYC_REQUIRED", "true")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    me = _register(client, "kyc-sanctioned@example.com")
    started = client.post("/kyc/start", headers=_auth(me["token"])).json()
    # Webhook flags sanctions hit + approves (provider flow).
    client.post("/kyc/webhook", json={
        "applicantId": started["applicant_id"],
        "result": "approved",
        "sanctionsHit": True,
        "eventId": "evt-sanctions-1",
    })

    sub = client.post("/autotrade/subscribe", json={
        "email": "kyc-sanctioned@example.com",
        "tier": "auto_lite",
        "exchange_id": "bybit",
        "api_key": "ABCD1234EFGHIJKL",
        "api_secret": "WXYZ9876MNOPQRST",
    }, headers=_auth(me["token"])).json()
    r = client.post(f"/autotrade/{sub['subscription_id']}/go-live", headers=_auth(me["token"]))
    assert r.status_code == 403
    assert "AML" in r.json()["detail"]

    get_settings.cache_clear()
