"""Custody / managed-pool tests.

Covers:
  * Pure-math: shares.issue / burn / share_price_from_aum / fee math
  * /wallet/me bootstrap (zero state)
  * /wallet/deposit-address gating (CUSTODY_LIVE_DEPOSITS_ENABLED)
  * /admin/treasury/credit-deposit (manual MVP credit, idempotent)
  * /admin/treasury/nav/snapshot — share-price recompute + perf-fee
  * /wallet/withdraw — burns shares, queues, cooldown after deposit
  * Withdrawal lifecycle: queued → approved → sent (or → cancelled)
  * Audit log writes per state mutation
"""
from __future__ import annotations


# ──────────────────────────── pure math ──────────────────────────── #


def test_issue_shares_bootstraps_to_one_when_no_nav():
    from app.custody.shares import issue_shares
    # First-deposit-ever: no NAV reference, share_price=0.0 → 1.0 fallback.
    assert issue_shares(100.0, 0.0) == 100.0
    assert issue_shares(100.0, 1.0) == 100.0


def test_issue_shares_at_premium():
    from app.custody.shares import issue_shares
    # Pool at 1.5x (e.g. up 50% from genesis), $100 buys ~66.67 shares.
    assert abs(issue_shares(100.0, 1.5) - 66.6667) < 0.01


def test_issue_shares_zero_amount():
    from app.custody.shares import issue_shares
    assert issue_shares(0.0, 1.5) == 0.0


def test_burn_shares_round_trip():
    from app.custody.shares import burn_shares, issue_shares
    # Symmetric: shares from $100 deposit then burn equivalent withdraw.
    sp = 1.25
    s = issue_shares(100.0, sp)
    s_burn = burn_shares(s * sp, sp)
    assert abs(s - s_burn) < 1e-9


def test_share_price_from_aum():
    from app.custody.shares import share_price_from_aum
    # 100 shares, $200 AUM → price 2.0 (50% gain).
    assert share_price_from_aum(200.0, 100.0) == 2.0
    # No shares yet → bootstrap.
    assert share_price_from_aum(0.0, 0.0) == 1.0
    assert share_price_from_aum(1000.0, 0.0) == 1.0


def test_performance_fee_only_on_gain_above_hwm():
    from app.custody.shares import performance_fee_shares
    # Below HWM → no fee.
    f, u, h = performance_fee_shares(100.0, 0.95, 1.00)
    assert f == 0.0 and u == 0.0 and h == 1.00
    # Equal HWM → no fee.
    f, u, h = performance_fee_shares(100.0, 1.00, 1.00)
    assert f == 0.0
    # 20% gain on 100 shares from price 1.00→1.20 → 20% of $20 = $4 fee.
    f, u, h = performance_fee_shares(100.0, 1.20, 1.00, perf_fee_pct=0.20)
    assert abs(u - 4.0) < 1e-9
    assert abs(f - (4.0 / 1.20)) < 1e-9
    assert h == 1.20


def test_management_fee_daily_pro_rata():
    from app.custody.shares import management_fee_shares
    # $10k position, 1 day at 2%/year → ~$0.5479.
    f, u = management_fee_shares(10000.0, 1.0, 1.0, annual_fee_pct=0.02)
    assert abs(u - 10000.0 * 0.02 / 365.0) < 1e-9
    # Zero days / zero balance → zero fee.
    assert management_fee_shares(0.0, 1.0, 1.0)[0] == 0.0
    assert management_fee_shares(10000.0, 1.0, 0.0)[0] == 0.0


# ──────────────────────────── helpers ────────────────────────────── #


def _register(client, email: str = "u@example.com", password: str = "pw1234567"):
    client.post("/auth/register", json={"email": email, "password": password})
    r = client.post("/auth/login", json={"email": email, "password": password})
    body = r.json()
    token = body.get("token") or body.get("access_token")
    if token:
        client.headers.update({"Authorization": f"Bearer {token}"})


def _kyc_approve(db, email: str):
    """Force-approve KYC for a registered user — custody endpoints are
    KYC-gated regardless of the global KYC_REQUIRED toggle."""
    from app.database.models import KycProfile, User

    u = db.query(User).filter(User.email == email).first()
    profile = db.query(KycProfile).filter(KycProfile.user_id == u.id).first()
    if profile is None:
        profile = KycProfile(user_id=u.id, status="approved")
    else:
        profile.status = "approved"
        profile.sanctions_hit = False
    db.add(profile)
    db.commit()


def _enable_custody(monkeypatch, request, *, with_license: bool = True):
    """Flip on the production gate AND set licence so deposit-address works."""
    from app.config.settings import get_settings
    monkeypatch.setenv("CUSTODY_LIVE_DEPOSITS_ENABLED", "true")
    if with_license:
        monkeypatch.setenv("CUSTODY_LICENSE_JURISDICTION", "Cayman Islands")
        monkeypatch.setenv("CUSTODY_LICENSE_NUMBER", "TEST-12345")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    request.addfinalizer(lambda: get_settings.cache_clear())  # type: ignore[attr-defined]


# ────────────────────────── /wallet/me ──────────────────────────── #


def test_wallet_me_bootstraps_zero_state(client_with_db):
    cl, _ = client_with_db
    _register(cl, "w1@example.com")
    r = cl.get("/wallet/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shares"] == 0.0
    assert body["share_price_usdt"] == 1.0
    assert body["equity_usdt"] == 0.0
    assert body["lifetime_deposit_usdt"] == 0.0


# ───────────────────── /wallet/deposit-address gating ──────────────── #


def test_deposit_address_503_when_custody_disabled(client_with_db):
    cl, _ = client_with_db
    _register(cl, "w2@example.com")
    r = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r.status_code == 503
    assert "CUSTODY_LIVE_DEPOSITS_ENABLED" in r.json()["detail"]


def test_deposit_address_503_when_no_license(client_with_db, monkeypatch, request):
    cl, _ = client_with_db
    _enable_custody(monkeypatch, request, with_license=False)
    _register(cl, "w3@example.com")
    r = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r.status_code == 503
    assert "licence" in r.json()["detail"]


def test_deposit_address_returns_address_when_enabled(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request, with_license=True)
    _register(cl, "w4@example.com")
    _kyc_approve(db, "w4@example.com")
    r = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chain"] == "trc20"
    assert body["address"].startswith("T") and len(body["address"]) >= 20
    assert body["memo"] is not None  # TRC20 supports memo
    assert body["asset"] == "USDT"
    # Idempotent — same address on second call.
    r2 = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r2.json()["address"] == body["address"]


def test_deposit_address_412_when_risk_ack_v2_not_signed(client_with_db, monkeypatch, request):
    """RISK_ACK_VERSION bumped to 2 when the custody section was added
    to /legal/disclosures.html. Clients on v1 (or zero) MUST re-ack
    before any /wallet/deposit-address call succeeds. Once the gate is
    enforced (COMPLIANCE_RISK_ACK_REQUIRED=true), an unsigned caller
    sees 412 — the UI catches it and replays POST /compliance/risk-ack
    with the current version."""
    cl, db = client_with_db
    _enable_custody(monkeypatch, request, with_license=True)
    monkeypatch.setenv("COMPLIANCE_RISK_ACK_REQUIRED", "true")
    _register(cl, "needs-ack@example.com")
    _kyc_approve(db, "needs-ack@example.com")
    r = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r.status_code == 412
    assert "risk acknowledgement" in r.json()["detail"]

    # After ack — request succeeds.
    current = cl.get("/compliance/risk-ack").json()["current_version"]
    assert current >= 2  # custody-section bump
    cl.post("/compliance/risk-ack", json={"version": current, "accepted": True})
    r = cl.post("/wallet/deposit-address", json={"chain": "trc20"})
    assert r.status_code == 200, r.text


def test_withdraw_412_when_risk_ack_v2_not_signed(client_with_db, monkeypatch, request):
    """Same gate as deposit-address — withdrawals also need v2 ack."""
    cl, db = client_with_db
    _enable_custody(monkeypatch, request, with_license=True)
    monkeypatch.setenv("COMPLIANCE_RISK_ACK_REQUIRED", "true")
    _register(cl, "wd-ack@example.com")
    _kyc_approve(db, "wd-ack@example.com")
    r = cl.post(
        "/wallet/withdraw",
        json={
            "chain": "trc20",
            "destination_address": "Tdest1234567890",
            "amount_usdt": 100.0,
        },
    )
    assert r.status_code == 412


def test_deposit_address_per_chain_unique(client_with_db, monkeypatch, request):
    cl, db = client_with_db
    _enable_custody(monkeypatch, request)
    _register(cl, "w5@example.com")
    _kyc_approve(db, "w5@example.com")
    addrs = {}
    for chain in ("trc20", "erc20", "ton", "sol", "bsc"):
        r = cl.post("/wallet/deposit-address", json={"chain": chain})
        assert r.status_code == 200, f"chain {chain}: {r.text}"
        addrs[chain] = r.json()["address"]
    # All distinct.
    assert len(set(addrs.values())) == 5


# ──────────────── admin treasury: credit-deposit + nav ────────────── #


def _promote_admin(db, email: str):
    from app.database.models import User
    u = db.query(User).filter(User.email == email).first()
    u.role = "admin"
    db.add(u)
    db.commit()


def test_credit_deposit_issues_shares_and_is_idempotent(client_with_db):
    cl, db = client_with_db
    # Register a normal client and then a separate admin caller.
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None)
    cl.cookies.clear()
    _register(cl, "admin@example.com")
    _promote_admin(db, "admin@example.com")
    # Re-login as admin since promotion happened after token issuance.
    cl.headers.pop("Authorization", None)
    cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})

    from app.database.models import User
    client_user = db.query(User).filter(User.email == "client@example.com").first()

    payload = {
        "user_id": client_user.id,
        "chain": "trc20",
        "tx_hash": "TXTEST123",
        "amount_usdt": 1000.0,
    }
    r = cl.post("/admin/treasury/credit-deposit", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credited"] is True
    assert abs(body["shares_credited"] - 1000.0) < 1e-6  # bootstrap @ 1.0
    assert body["idempotent"] is False

    # Idempotent on re-credit with same TX.
    r = cl.post("/admin/treasury/credit-deposit", json=payload)
    assert r.status_code == 200
    assert r.json()["idempotent"] is True

    # Pool view reflects the credit.
    r = cl.get("/admin/treasury/pool")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["client_count"] == 1
    assert p["by_chain"]["trc20"]["total_in_usdt"] == 1000.0


def test_nav_snapshot_recomputes_share_price_and_accrues_fees(client_with_db):
    """Bootstrap a 1000 USDT pool with 1000 shares, then mark AUM up to
    1500 (50% pool gain). Share-price → 1.5. Performance fee = 20% of
    (1.5 - 1.0) * 1000 shares = $100, taken as 100/1.5 ≈ 66.67 shares.
    Client's remaining shares: 1000 - 66.67 ≈ 933.33."""
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None)
    cl.cookies.clear()
    _register(cl, "admin@example.com")
    _promote_admin(db, "admin@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})

    from app.database.models import ClientWallet, User
    client_user = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": client_user.id, "chain": "trc20",
        "tx_hash": "TX001", "amount_usdt": 1000.0,
    })

    # NAV up 50% → fee fires.
    r = cl.post("/admin/treasury/nav/snapshot", json={"aum_usdt": 1500.0, "note": "test"})
    assert r.status_code == 200, r.text
    snap = r.json()
    assert abs(snap["share_price_usdt"] - 1.5) < 1e-6

    db.expire_all()
    wallet = db.query(ClientWallet).filter(ClientWallet.user_id == client_user.id).first()
    # Client's shares reduced by ~66.67 fee shares.
    assert abs(float(wallet.shares) - (1000.0 - 100.0 / 1.5)) < 0.5
    assert abs(float(wallet.hwm_share_price) - 1.5) < 1e-6

    # Treasury wallet now holds the fee shares so total_shares is invariant.
    treasury_user = db.query(User).filter(User.email == "treasury@signalx.internal").first()
    assert treasury_user is not None and treasury_user.is_active is False
    treasury_wallet = (
        db.query(ClientWallet)
        .filter(ClientWallet.user_id == treasury_user.id)
        .first()
    )
    assert treasury_wallet is not None
    assert abs(float(treasury_wallet.shares) - 100.0 / 1.5) < 0.5
    # Sum of all shares equals the original 1000 (transfer, not burn).
    total = db.query(ClientWallet).all()
    assert abs(sum(float(w.shares) for w in total) - 1000.0) < 1e-6

    # PerformanceFee row records the pre/post HWM correctly.
    from app.database.models import PerformanceFee
    pf = (
        db.query(PerformanceFee)
        .filter(PerformanceFee.user_id == client_user.id)
        .order_by(PerformanceFee.id.desc())
        .first()
    )
    assert pf is not None
    assert abs(float(pf.hwm_before) - 1.0) < 1e-6
    assert abs(float(pf.hwm_after) - 1.5) < 1e-6

    # Pool view excludes treasury from client_count.
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})
    pool = cl.get("/admin/treasury/pool").json()
    assert pool["client_count"] == 1
    assert pool["treasury_fee_shares"] > 0


# ──────────────────────── /wallet/withdraw flow ───────────────────── #


def test_withdraw_requires_balance_and_burns_shares(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    _kyc_approve(db, "client@example.com")
    # Withdraw with no balance → 400.
    r = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "TXTOWALLET" + "0" * 25,
        "amount_usdt": 100.0,
    })
    assert r.status_code == 400


def test_withdraw_blocked_by_cooldown_after_deposit(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    _kyc_approve(db, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, "admin@example.com")
    _promote_admin(db, "admin@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})

    from app.database.models import User
    client_user = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": client_user.id, "chain": "trc20",
        "tx_hash": "TX001", "amount_usdt": 1000.0,
    })
    # Switch to client.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "client@example.com", "password": "pw1234567"})
    r = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "T" + "x" * 33,
        "amount_usdt": 100.0,
    })
    assert r.status_code == 409  # cooldown active


def test_withdraw_lifecycle_queue_approve_send(client_with_db):
    """End-to-end withdrawal: client deposits, admin clears cooldown by
    backdating, client withdraws, admin approves, admin sends with TX."""
    from datetime import datetime, timedelta

    cl, db = client_with_db
    _register(cl, "client@example.com")
    _kyc_approve(db, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, "admin@example.com")
    _promote_admin(db, "admin@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})

    from app.database.models import Deposit, User, Withdrawal
    client_user = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": client_user.id, "chain": "trc20",
        "tx_hash": "TX001", "amount_usdt": 1000.0,
    })

    # Backdate the deposit so the cooldown has expired.
    dep = db.query(Deposit).filter(Deposit.tx_hash == "TX001").first()
    dep.credited_at = datetime.utcnow() - timedelta(days=2)
    db.add(dep); db.commit()

    # Switch to client and withdraw.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "client@example.com", "password": "pw1234567"})
    r = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "T" + "x" * 33,
        "amount_usdt": 200.0,
    })
    assert r.status_code == 200, r.text
    wd_id = r.json()["withdrawal_id"]
    # Shares are burned immediately on /withdraw.
    r = cl.get("/wallet/me")
    assert abs(r.json()["shares"] - 800.0) < 1e-6

    # Admin approve + send.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})
    r = cl.post(f"/admin/treasury/withdrawals/{wd_id}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = cl.post(f"/admin/treasury/withdrawals/{wd_id}/send", json={"tx_hash": "OUTTX42"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert r.json()["tx_hash"] == "OUTTX42"

    db.expire_all()
    wd = db.query(Withdrawal).filter(Withdrawal.id == wd_id).first()
    assert wd.status == "sent"
    # Lifetime withdraw on the wallet updated.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "client@example.com", "password": "pw1234567"})
    me = cl.get("/wallet/me").json()
    assert abs(me["lifetime_withdraw_usdt"] - 200.0) < 1e-6


def test_withdraw_cancel_re_credits_shares(client_with_db):
    from datetime import datetime, timedelta
    cl, db = client_with_db
    _register(cl, "client@example.com")
    _kyc_approve(db, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, "admin@example.com")
    _promote_admin(db, "admin@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})

    from app.database.models import Deposit, User
    client_user = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": client_user.id, "chain": "trc20",
        "tx_hash": "TX001", "amount_usdt": 1000.0,
    })
    dep = db.query(Deposit).filter(Deposit.tx_hash == "TX001").first()
    dep.credited_at = datetime.utcnow() - timedelta(days=2)
    db.add(dep); db.commit()

    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "client@example.com", "password": "pw1234567"})
    r = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "T" + "x" * 33,
        "amount_usdt": 200.0,
    })
    wd_id = r.json()["withdrawal_id"]
    me_pre = cl.get("/wallet/me").json()
    assert abs(me_pre["shares"] - 800.0) < 1e-6

    # Admin cancels.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "admin@example.com", "password": "pw1234567"})
    r = cl.post(
        f"/admin/treasury/withdrawals/{wd_id}/cancel",
        json={"reason": "AML hit on destination address"},
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelled"

    # Client's shares restored.
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "client@example.com", "password": "pw1234567"})
    me_post = cl.get("/wallet/me").json()
    assert abs(me_post["shares"] - 1000.0) < 1e-6


# ─────────────── pending-deposits / reattribute / audit-log ────────── #


def _admin_login(cl, db, email: str = "admin@example.com"):
    """Register + promote + login as admin. Returns admin user id."""
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, email)
    _promote_admin(db, email)
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": email, "password": "pw1234567"})


def test_deposits_pending_lists_uncredited_only(client_with_db):
    cl, db = client_with_db
    # Seed: one credited deposit, one uncredited.
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import Deposit, User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": cu.id, "chain": "trc20", "tx_hash": "OK1", "amount_usdt": 100.0,
    })
    db.add(Deposit(
        user_id=cu.id, chain="trc20", tx_hash="PEND1",
        amount_usdt=2.0, credited=False,
    ))
    db.commit()

    r = cl.get("/admin/treasury/deposits/pending")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tx_hash"] == "PEND1"
    assert items[0]["is_unattributed"] is False  # owned by real user, just below-min


def test_deposit_reattribute_credits_shares_and_audits(client_with_db):
    cl, db = client_with_db
    _register(cl, "newowner@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import (
        AmlEvent, ClientWallet, Deposit, User,
    )
    target = db.query(User).filter(User.email == "newowner@example.com").first()

    # Create an unattributed deposit on the sentinel user.
    sentinel = User(
        email="unassigned@signalx.internal",
        password_hash="!disabled",
        role="admin",
        is_active=False,
    )
    db.add(sentinel); db.flush()
    dep = Deposit(
        user_id=sentinel.id, chain="trc20", tx_hash="ORPHAN1",
        amount_usdt=300.0, credited=False,
    )
    db.add(dep); db.commit()

    r = cl.post(
        f"/admin/treasury/deposits/{dep.id}/reattribute",
        json={"user_id": target.id, "note": "matched via Tron explorer"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credited"] is True
    assert body["user_id"] == target.id
    assert abs(body["shares_credited"] - 300.0) < 1e-6  # bootstrap NAV

    # Wallet now holds 300 shares.
    db.expire_all()
    w = db.query(ClientWallet).filter(ClientWallet.user_id == target.id).first()
    assert abs(float(w.shares) - 300.0) < 1e-6

    # Audit row written.
    audit = (
        db.query(AmlEvent)
        .filter(AmlEvent.kind == "custody_deposit_reattributed")
        .first()
    )
    assert audit is not None
    assert audit.user_id == target.id
    assert "matched via Tron explorer" in (audit.detail or "")


def test_deposit_reattribute_refuses_already_credited(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    r = cl.post("/admin/treasury/credit-deposit", json={
        "user_id": cu.id, "chain": "trc20",
        "tx_hash": "ALREADY1", "amount_usdt": 100.0,
    })
    dep_id = r.json()["id"]
    r = cl.post(
        f"/admin/treasury/deposits/{dep_id}/reattribute",
        json={"user_id": cu.id, "note": "no-op"},
    )
    assert r.status_code == 409


def test_deposit_reattribute_refuses_internal_user(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import Deposit, User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    treasury = User(
        email="treasury@signalx.internal",
        password_hash="!disabled",
        role="admin",
        is_active=False,
    )
    db.add(treasury); db.flush()
    dep = Deposit(
        user_id=cu.id, chain="trc20", tx_hash="REF1",
        amount_usdt=100.0, credited=False,
    )
    db.add(dep); db.commit()
    r = cl.post(
        f"/admin/treasury/deposits/{dep.id}/reattribute",
        json={"user_id": treasury.id, "note": "should be refused"},
    )
    assert r.status_code == 400


def test_audit_log_returns_custody_events_only(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import AmlEvent, User
    # Seed unrelated AML events that should NOT appear.
    db.add(AmlEvent(user_id=None, actor_id=None, kind="kyc_status_change", detail="x"))
    db.add(AmlEvent(user_id=None, actor_id=None, kind="custody_test_event", detail="y"))
    db.commit()
    r = cl.get("/admin/treasury/audit-log")
    assert r.status_code == 200
    kinds = {item["kind"] for item in r.json()["items"]}
    assert all(k.startswith("custody_") for k in kinds)
    assert "custody_test_event" in kinds
    assert "kyc_status_change" not in kinds


def test_audit_log_filters_by_kind(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": cu.id, "chain": "trc20", "tx_hash": "TX-FILTER",
        "amount_usdt": 50.0,
    })
    r = cl.get(
        "/admin/treasury/audit-log",
        params={"kind": "custody_deposit_credited"},
    )
    assert r.status_code == 200
    kinds = {item["kind"] for item in r.json()["items"]}
    assert kinds == {"custody_deposit_credited"}


# ───────────────────────── treasury health ────────────────────────── #


def test_treasury_health_ok_on_empty_state(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    r = cl.get("/admin/treasury/health")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_status"] == "ok"
    assert body["shares_invariant_ok"] is True
    assert body["negative_balances"] == 0
    assert body["pending_deposit_count"] == 0
    assert body["unattributed_count"] == 0
    assert body["pending_withdrawal_count"] == 0


def test_treasury_health_warn_on_unattributed_deposit(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import Deposit, User
    sentinel = User(
        email="unassigned@signalx.internal",
        password_hash="!disabled",
        role="admin",
        is_active=False,
    )
    db.add(sentinel); db.flush()
    db.add(Deposit(
        user_id=sentinel.id, chain="trc20", tx_hash="ORPHAN-HEALTH",
        amount_usdt=100.0, credited=False,
    ))
    db.commit()
    r = cl.get("/admin/treasury/health")
    assert r.status_code == 200
    body = r.json()
    assert body["unattributed_count"] == 1
    assert body["pending_deposit_count"] == 1
    assert body["overall_status"] == "warn"


def test_withdrawal_send_rejects_duplicate_tx_hash(client_with_db):
    """Two distinct withdrawals must not both record the same on-chain
    tx_hash as `sent`. Pre-check returns 409 with the conflicting row id;
    DB-level UniqueConstraint on Withdrawal(chain, tx_hash) is the
    source-of-truth fallback for races."""
    from datetime import datetime, timedelta

    cl, db = client_with_db
    _register(cl, "wd-dup@example.com")
    _kyc_approve(db, "wd-dup@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _register(cl, "ad-dup@example.com")
    _promote_admin(db, "ad-dup@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "ad-dup@example.com", "password": "pw1234567"})

    from app.database.models import Deposit, User, Withdrawal
    cu = db.query(User).filter(User.email == "wd-dup@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": cu.id, "chain": "trc20", "tx_hash": "DEP1", "amount_usdt": 1000.0,
    })
    d = db.query(Deposit).filter(Deposit.tx_hash == "DEP1").first()
    d.credited_at = datetime.utcnow() - timedelta(days=2)
    db.add(d); db.commit()

    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "wd-dup@example.com", "password": "pw1234567"})
    wd1 = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "T" + "1" * 33, "amount_usdt": 100.0,
    }).json()["withdrawal_id"]
    wd2 = cl.post("/wallet/withdraw", json={
        "chain": "trc20", "destination_address": "T" + "2" * 33, "amount_usdt": 100.0,
    }).json()["withdrawal_id"]

    cl.headers.pop("Authorization", None); cl.cookies.clear()
    cl.post("/auth/login", json={"email": "ad-dup@example.com", "password": "pw1234567"})
    cl.post(f"/admin/treasury/withdrawals/{wd1}/approve")
    cl.post(f"/admin/treasury/withdrawals/{wd2}/approve")

    r1 = cl.post(f"/admin/treasury/withdrawals/{wd1}/send", json={"tx_hash": "ONCHAIN-XYZ"})
    assert r1.status_code == 200

    r2 = cl.post(f"/admin/treasury/withdrawals/{wd2}/send", json={"tx_hash": "ONCHAIN-XYZ"})
    assert r2.status_code == 409
    assert str(wd1) in r2.json()["detail"]

    db.expire_all()
    wd2_row = db.query(Withdrawal).filter(Withdrawal.id == wd2).first()
    assert wd2_row.status == "approved"
    assert wd2_row.tx_hash is None


def test_treasury_health_flags_signature_failure_spike(client_with_db):
    """Webhook signature failures in last 1h roll into the health status:
    >=3 → warn, >=10 → critical. Operator sees the count in the dashboard
    detail line and rotates the secret."""
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import AmlEvent

    for i in range(4):
        db.add(AmlEvent(
            user_id=None, actor_id=None,
            kind="custody_deposit_webhook_signature_invalid",
            detail=f'{{"chain":"trc20","ip":"1.2.3.{i}","status":401}}',
        ))
    db.commit()

    r = cl.get("/admin/treasury/health")
    assert r.status_code == 200
    body = r.json()
    assert body["webhook_signature_failures_1h"] == 4
    assert body["overall_status"] == "warn"

    for i in range(7):
        db.add(AmlEvent(
            user_id=None, actor_id=None,
            kind="custody_deposit_webhook_signature_invalid",
            detail=f'{{"chain":"erc20","ip":"5.6.7.{i}","status":401}}',
        ))
    db.commit()

    r2 = cl.get("/admin/treasury/health")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["webhook_signature_failures_1h"] >= 10
    assert body2["overall_status"] == "critical"


def test_treasury_health_critical_on_negative_balance(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import ClientWallet, User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    db.add(ClientWallet(
        user_id=cu.id, shares=-1.0, balance_usdt=-1.0, hwm_share_price=1.0,
    ))
    db.commit()
    r = cl.get("/admin/treasury/health")
    assert r.status_code == 200
    body = r.json()
    assert body["negative_balances"] == 1
    assert body["overall_status"] == "critical"


# ───────────────────────── CSV exports ────────────────────────────── #


def test_audit_log_csv_export(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    cl.post("/admin/treasury/credit-deposit", json={
        "user_id": cu.id, "chain": "trc20", "tx_hash": "TX-CSV",
        "amount_usdt": 75.0,
    })

    r = cl.get("/admin/treasury/audit-log.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    lines = [ln for ln in body.replace("\r", "").split("\n") if ln]
    assert lines[0].split(",")[:6] == ["id", "created_at", "kind", "user_id", "actor_id", "detail"]
    assert "custody_deposit_credited" in body
    assert "TX-CSV" in body


def test_wallets_csv_excludes_internal_users(client_with_db):
    cl, db = client_with_db
    _register(cl, "client@example.com")
    cl.headers.pop("Authorization", None); cl.cookies.clear()
    _admin_login(cl, db)
    from app.database.models import ClientWallet, User
    cu = db.query(User).filter(User.email == "client@example.com").first()
    # Seed sentinel + treasury users with wallets — they MUST NOT appear in CSV.
    sentinel = User(
        email="unassigned@signalx.internal", password_hash="!d", role="admin", is_active=False,
    )
    treasury = User(
        email="treasury@signalx.internal", password_hash="!d", role="admin", is_active=False,
    )
    db.add_all([sentinel, treasury]); db.flush()
    db.add_all([
        ClientWallet(user_id=cu.id, shares=100.0, balance_usdt=100.0, hwm_share_price=1.0),
        ClientWallet(user_id=sentinel.id, shares=10.0, balance_usdt=10.0, hwm_share_price=1.0),
        ClientWallet(user_id=treasury.id, shares=5.0, balance_usdt=5.0, hwm_share_price=1.0),
    ])
    db.commit()

    r = cl.get("/admin/treasury/wallets.csv")
    assert r.status_code == 200
    body = r.text
    lines = [ln for ln in body.replace("\r", "").split("\n") if ln]
    # Header + 1 client row only.
    assert len(lines) == 2
    assert "user_id" in lines[0]
    assert lines[1].startswith(f"{cu.id},")
