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
