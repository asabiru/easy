"""Chain-deposit webhook tests.

Covers the five webhook endpoints + the shared signature helpers in
`app/custody/webhooks.py`. Real chain integration is mocked (we never
hit TronGrid / Alchemy / Helius / TON / BSCscan from the test box).

What we explicitly verify:
  * Master toggle (`CUSTODY_LIVE_DEPOSITS_ENABLED`) gates ALL chains.
  * Per-chain webhook secret gates each chain individually.
  * Signature mismatch → 401, no DB writes.
  * Valid signature + matching memo/address → idempotent share credit.
  * Below-min amount → row recorded but `credited=False` (operator
    review path).
  * Unmatched memo + unmatched address → routed to the unattributed
    sentinel user.
  * Re-delivery of the same `(chain, tx_hash)` is a no-op (idempotency
    is the entire reason webhooks exist — providers retry on any
    non-2xx, so we MUST be safe under retry storms).
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest


# ───────────────────────── helpers (shared) ──────────────────────── #


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _enable_custody(monkeypatch, request):
    """Flip on the master toggle + minimum licence config."""
    from app.config.settings import get_settings
    monkeypatch.setenv("CUSTODY_LIVE_DEPOSITS_ENABLED", "true")
    monkeypatch.setenv("CUSTODY_LICENSE_JURISDICTION", "Cayman Islands")
    monkeypatch.setenv("CUSTODY_LICENSE_NUMBER", "TEST-12345")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    request.addfinalizer(lambda: get_settings.cache_clear())  # type: ignore[attr-defined]


def _set_secret(monkeypatch, key: str, value: str):
    monkeypatch.setenv(key, value)
    from app.config.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _register(client, email: str = "user@example.com"):
    client.post("/auth/register", json={"email": email, "password": "pw1234567"})
    r = client.post("/auth/login", json={"email": email, "password": "pw1234567"})
    body = r.json()
    token = body.get("token") or body.get("access_token")
    if token:
        client.headers.update({"Authorization": f"Bearer {token}"})


def _allocate_address(db, user_email: str, chain: str, address: str, memo: str | None = None):
    """Seed a DepositAddress row directly so webhooks can route to it."""
    from app.database.models import DepositAddress, User
    u = db.query(User).filter(User.email == user_email).first()
    addr = DepositAddress(
        user_id=u.id,
        chain=chain,
        external_address=address,
        memo=memo,
        derivation_path="m/44'/195'/0'/0/test",
    )
    db.add(addr)
    db.commit()
    return u.id


# ───────────────── master toggle / secret gates ─────────────────── #


def test_webhook_503_when_master_toggle_off(client_with_db, monkeypatch):
    """No CUSTODY_LIVE_DEPOSITS_ENABLED → all 5 chains 503."""
    cl, _ = client_with_db
    monkeypatch.setenv("CUSTODY_LIVE_DEPOSITS_ENABLED", "false")
    from app.config.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]
    for path in (
        "/payments/trongrid/webhook",
        "/payments/alchemy/webhook",
        "/payments/helius/webhook",
        "/payments/bsc/webhook",
        "/payments/ton-pool/webhook",
    ):
        r = cl.post(path, json={})
        assert r.status_code == 503, path
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_webhook_503_when_chain_secret_missing(client_with_db, monkeypatch, request):
    """Master on, but chain secret unset → that chain returns 503."""
    cl, _ = client_with_db
    _enable_custody(monkeypatch, request)
    # No CUSTODY_TRONGRID_WEBHOOK_SECRET set.
    body = json.dumps({"transaction_id": "x", "to_address": "Tabc"}).encode()
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": "deadbeef"},
    )
    assert r.status_code == 503


# ─────────────────────────── TRC20 (TronGrid) ───────────────────── #


def test_trongrid_webhook_signature_mismatch_401(client_with_db, monkeypatch, request):
    cl, _ = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "topsecret")
    body = json.dumps({"transaction_id": "tx1", "to_address": "Tabc"}).encode()
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": "ffffffffffffff"},
    )
    assert r.status_code == 401


def test_trongrid_webhook_credits_matched_user_by_memo(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "trongrid-shh")
    _register(cl, "trc-user@example.com")
    user_id = _allocate_address(db, "trc-user@example.com", "trc20", "Ttreasury", "SX-trc")

    payload = {
        "transaction_id": "tx-trc-1",
        "to_address": "Ttreasury",
        "memo": "SX-trc",
        "value": str(int(500 * 1_000_000)),  # 500 USDT, 6 decimals
        "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        "confirmed": True,
    }
    body = json.dumps(payload).encode()
    sig = _sign("trongrid-shh", body)
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["credited"] is True
    assert out["idempotent"] is False
    assert out["chain"] == "trc20"

    # Idempotent re-delivery → 200, idempotent=True, no double-credit.
    r2 = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": sig, "content-type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True

    # Wallet should hold exactly 500 shares (bootstrap NAV).
    from app.database.models import ClientWallet
    w = db.query(ClientWallet).filter(ClientWallet.user_id == user_id).first()
    assert abs(float(w.shares) - 500.0) < 1e-6


def test_trongrid_webhook_below_min_records_uncredited(client_with_db, monkeypatch, request):
    """A $1 TRC20 deposit is below the $5 minimum → row stored but
    `credited=False`. Audit trail still surfaces the inbound TX."""
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "shh")
    _register(cl, "small@example.com")
    _allocate_address(db, "small@example.com", "trc20", "Ttiny", "SX-tiny")
    payload = {
        "transaction_id": "tx-small",
        "to_address": "Ttiny",
        "memo": "SX-tiny",
        "value": str(int(1.0 * 1_000_000)),  # 1 USDT — below 5 USDT min
        "confirmed": True,
    }
    body = json.dumps(payload).encode()
    sig = _sign("shh", body)
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["credited"] is False


def test_trongrid_webhook_rejects_non_usdt_contract(client_with_db, monkeypatch, request):
    cl, _ = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "k")
    payload = {
        "transaction_id": "tx-bad",
        "to_address": "Tx",
        "value": "1000000",
        "contract_address": "TWrongContractAddressxxxx",
    }
    body = json.dumps(payload).encode()
    sig = _sign("k", body)
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 400


# ─────────────────────────── ERC20 (Alchemy) ────────────────────── #


def test_alchemy_webhook_credits_matched_user_by_address(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_ALCHEMY_WEBHOOK_SECRET", "alchemy-shh")
    _register(cl, "erc-user@example.com")
    user_id = _allocate_address(db, "erc-user@example.com", "erc20", "0xabcdef")

    payload = {
        "event": {
            "activity": [{
                "hash": "0xtxerc",
                "toAddress": "0xabcdef",
                "value": 250.0,
                "rawContract": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7"},
            }]
        }
    }
    body = json.dumps(payload).encode()
    sig = _sign("alchemy-shh", body)
    r = cl.post(
        "/payments/alchemy/webhook",
        content=body,
        headers={"x-alchemy-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["credited"] is True
    assert out["chain"] == "erc20"


# ──────────────────────────── Solana (Helius) ───────────────────── #


def test_helius_webhook_bearer_match_credits(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_HELIUS_WEBHOOK_SECRET", "helius-bearer")
    _register(cl, "sol-user@example.com")
    user_id = _allocate_address(db, "sol-user@example.com", "sol", "SoLAccountAbc", "SX-sol")

    payload = [{
        "signature": "sig-sol-1",
        "memo": "SX-sol",
        "tokenTransfers": [{
            "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "toUserAccount": "SoLAccountAbc",
            "tokenAmount": 100.0,
        }],
    }]
    r = cl.post(
        "/payments/helius/webhook",
        json=payload,
        headers={"authorization": "Bearer helius-bearer"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["credited"] is True


def test_helius_webhook_bad_bearer_401(client_with_db, monkeypatch, request):
    cl, _ = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_HELIUS_WEBHOOK_SECRET", "right")
    r = cl.post(
        "/payments/helius/webhook",
        json=[{"signature": "x", "tokenTransfers": [{}]}],
        headers={"authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


# ─────────────────────────── BSC (custom) ───────────────────────── #


def test_bsc_webhook_credits_matched_user(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_BSCSCAN_WEBHOOK_SECRET", "bsc-shh")
    _register(cl, "bsc-user@example.com")
    _allocate_address(db, "bsc-user@example.com", "bsc", "0xbscaddr")
    payload = {
        "transaction_hash": "0xbsctx",
        "to_address": "0xbscaddr",
        "value": str(int(50 * 1e18)),  # 50 USDT, 18 decimals
        "contract_address": "0x55d398326f99059ff775485246999027b3197955",
        "confirmed": True,
    }
    body = json.dumps(payload).encode()
    sig = _sign("bsc-shh", body)
    r = cl.post(
        "/payments/bsc/webhook",
        content=body,
        headers={"x-bsc-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["credited"] is True


# ─────────────────── unmatched / unattributed routing ────────────── #


def test_webhook_unmatched_routes_to_sentinel(client_with_db, monkeypatch, request):
    """No DepositAddress for the inbound memo/address → routed to the
    `unassigned@signalx.internal` sentinel user; credited=False."""
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "k")
    payload = {
        "transaction_id": "tx-orphan",
        "to_address": "Tnotours",
        "memo": "SX-unknown",
        "value": str(int(50 * 1_000_000)),
        "confirmed": True,
    }
    body = json.dumps(payload).encode()
    sig = _sign("k", body)
    r = cl.post(
        "/payments/trongrid/webhook",
        content=body,
        headers={"x-trongrid-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["credited"] is False
    # Sentinel user owns the orphan deposit.
    from app.database.models import Deposit, User
    sentinel = db.query(User).filter(
        User.email == "unassigned@signalx.internal"
    ).first()
    assert sentinel is not None
    assert sentinel.is_active is False
    dep = db.query(Deposit).filter(Deposit.tx_hash == "tx-orphan").first()
    assert dep is not None
    assert dep.user_id == sentinel.id
    assert dep.credited is False


# ───────────────────────── pure verifiers ───────────────────────── #


def test_verify_hmac_sha256_constant_time_compare():
    from app.custody.webhooks import verify_hmac_sha256
    body = b'{"a":1}'
    sig = hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256("k", body, sig) is True
    assert verify_hmac_sha256("k", body, "f" * 64) is False
    assert verify_hmac_sha256("", body, sig) is False
    assert verify_hmac_sha256("k", body, "") is False


def test_verify_bearer_strips_prefix():
    from app.custody.webhooks import verify_bearer
    assert verify_bearer("xyz", "Bearer xyz") is True
    assert verify_bearer("xyz", "xyz") is True
    assert verify_bearer("xyz", "Bearer wrong") is False
    assert verify_bearer("", "Bearer xyz") is False
    assert verify_bearer("xyz", "") is False


# ──────────── full lifecycle: orphan webhook → operator reattribute ─── #


def _admin_login(cl, db, email: str = "admin-lifecycle@example.com"):
    """Register + promote + login as admin. Same shape as the helper in
    test_custody.py — duplicated to avoid cross-file imports."""
    from app.database.models import User
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, email)
    u = db.query(User).filter(User.email == email).first()
    u.role = "admin"
    db.commit()
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": email, "password": "pw1234567"})


def test_full_webhook_orphan_then_operator_reattribute_credits_shares(
    client_with_db, monkeypatch, request,
):
    """End-to-end QA flow: an unmatched TronGrid deposit is sentinel-routed,
    operator reattributes it via the admin endpoint, shares are credited,
    audit trail is correct, second webhook delivery of the same TX is
    a no-op (idempotent)."""
    from app.config.settings import get_settings
    from app.database.models import (
        AmlEvent, ClientWallet, Deposit, DepositAddress, User,
    )

    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _set_secret(monkeypatch, "CUSTODY_TRONGRID_WEBHOOK_SECRET", "secret-trc")

    # 1. Real client exists but has no DepositAddress → webhook orphan.
    _register(cl, "real-client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()

    # 2. Webhook arrives — 100 USDT, no match.
    secret = get_settings().custody_trongrid_webhook_secret
    payload = {
        "transaction_id": "TX-LIFECYCLE-1",
        "to_address": "TUNKNOWN_ADDRESS",
        "memo": "no-such-memo",
        "value": "100000000",  # 100 USDT in 6dp
        "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        "confirmed": True,
    }
    body = json.dumps(payload).encode()
    sig = _sign(secret, body)
    r = cl.post(
        "/payments/trongrid/webhook", data=body,
        headers={
            "x-trongrid-signature": sig,
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert j["credited"] is False
    assert j["chain"] == "trc20"
    deposit_id = j["deposit_id"]

    # 3. Sentinel-routed: deposit is anchored to unassigned@signalx.internal.
    sentinel = db.query(User).filter(User.email == "unassigned@signalx.internal").first()
    assert sentinel is not None, "sentinel user must be auto-created"
    dep = db.query(Deposit).filter(Deposit.id == deposit_id).first()
    assert dep.user_id == sentinel.id
    assert dep.credited is False

    # 4. Re-delivery → idempotent, same row, no second audit entry.
    r2 = cl.post(
        "/payments/trongrid/webhook", data=body,
        headers={
            "x-trongrid-signature": sig,
            "content-type": "application/json",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    assert r2.json()["deposit_id"] == deposit_id
    assert db.query(Deposit).filter(Deposit.tx_hash == "TX-LIFECYCLE-1").count() == 1

    # 5. Operator visits /admin/treasury/deposits/pending — sees orphan.
    _admin_login(cl, db)
    pending = cl.get("/admin/treasury/deposits/pending").json()
    pend_ids = [it["id"] for it in pending["items"]]
    assert deposit_id in pend_ids
    orphan_row = next(it for it in pending["items"] if it["id"] == deposit_id)
    assert orphan_row["is_unattributed"] is True

    # 6. Operator reattributes to the real client.
    real = db.query(User).filter(User.email == "real-client@example.com").first()
    r3 = cl.post(
        f"/admin/treasury/deposits/{deposit_id}/reattribute",
        json={
            "user_id": real.id,
            "note": "Verified TX on Tronscan; client confirmed via support ticket #4471",
        },
    )
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["credited"] is True
    assert body3["shares_credited"] > 0

    # 7. Deposit row now points to real user, credited=True.
    db.expire_all()
    dep2 = db.query(Deposit).filter(Deposit.id == deposit_id).first()
    assert dep2.user_id == real.id
    assert dep2.credited is True

    # 8. ClientWallet exists for real user, shares > 0.
    wallet = db.query(ClientWallet).filter(ClientWallet.user_id == real.id).first()
    assert wallet is not None
    assert float(wallet.shares) > 0
    assert float(wallet.balance_usdt) > 0

    # 9. Audit trail: webhook_pending_review + deposit_reattributed both recorded.
    audit_kinds = {
        e.kind for e in db.query(AmlEvent).filter(AmlEvent.kind.like("custody_%")).all()
    }
    assert "custody_deposit_webhook_pending_review" in audit_kinds
    assert "custody_deposit_reattributed" in audit_kinds

    # 10. Pending queue no longer lists this deposit.
    pending2 = cl.get("/admin/treasury/deposits/pending").json()
    assert deposit_id not in [it["id"] for it in pending2["items"]]

    # 11. CSV audit-log mirrors the JSON audit and includes our reattribute event.
    csv_resp = cl.get("/admin/treasury/audit-log.csv")
    assert csv_resp.status_code == 200
    assert "custody_deposit_reattributed" in csv_resp.text

    # 12. Treasury health: invariant ok, 0 unattributed remaining.
    h = cl.get("/admin/treasury/health").json()
    assert h["unattributed_count"] == 0
    assert h["negative_balances"] == 0
