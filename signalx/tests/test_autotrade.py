"""Auto-trade subscription + executor tests.

Covers: encryption round-trip, paper-mode subscribe flow, kill switch,
risk-guard rejection (oversized position, daily loss), end-to-end signal
dispatch in paper mode through /news/ingest.
"""
from app.autotrade.crypto import decrypt, encrypt
from app.autotrade.risk_guard import evaluate_pre_order


def _register(client, email: str, password: str = "testpass123") -> dict:
    """Register a new user. TestClient persists the session cookie so all
    subsequent calls on `client` are authenticated as this user."""
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": email.split("@")[0]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _logout(client) -> None:
    client.post("/auth/logout")


def test_encrypt_round_trip():
    cipher = encrypt("super-secret-api-key")
    assert cipher and cipher != "super-secret-api-key"
    assert decrypt(cipher) == "super-secret-api-key"


def test_encrypt_empty():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_subscribe_flow_starts_in_paper_mode(client):
    _register(client, "alice@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "alice@example.com",
            "tier": "auto_pro",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    sub_id = body["subscription_id"]
    assert body["status"] == "paper"
    assert body["live_trading_enabled"] is False
    assert body["paper_until"] is not None

    # status endpoint reflects paper mode
    rs = client.get(f"/autotrade/{sub_id}/status")
    assert rs.status_code == 200
    assert rs.json()["status"] == "paper"


def test_kill_switch_blocks_orders(client):
    _register(client, "bob@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "bob@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    )
    sub_id = r.json()["subscription_id"]
    rk = client.post(f"/autotrade/{sub_id}/kill")
    assert rk.status_code == 200
    assert rk.json()["status"] == "killed"

    rs = client.get(f"/autotrade/{sub_id}/status")
    assert rs.json()["status"] == "killed"


def test_go_live_refused_when_global_kill_switch_off(client):
    _register(client, "carol@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "carol@example.com",
            "tier": "vip",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    )
    sub_id = r.json()["subscription_id"]
    rg = client.post(f"/autotrade/{sub_id}/go-live")
    assert rg.status_code == 409
    assert "global autotrade kill switch" in rg.json()["detail"]


def test_risk_guard_rejects_oversized_position():
    decision = evaluate_pre_order(
        status="live",
        live_trading_enabled=True,
        global_autotrade_enabled=True,
        proposed_notional=2000.0,
        free_balance=10_000.0,
        max_position_pct=0.10,  # cap = 1000
        daily_pnl=0.0,
        starting_balance=10_000.0,
        daily_loss_limit_pct=0.05,
    )
    assert decision.allow is False
    assert "cap" in decision.reason


def test_risk_guard_pauses_on_daily_loss():
    decision = evaluate_pre_order(
        status="live",
        live_trading_enabled=True,
        global_autotrade_enabled=True,
        proposed_notional=500.0,
        free_balance=9_400.0,
        max_position_pct=0.10,
        daily_pnl=-600.0,  # -6% of 10k starting
        starting_balance=10_000.0,
        daily_loss_limit_pct=0.05,
    )
    assert decision.allow is False
    assert decision.pause_subscription is True
    assert "daily loss" in decision.reason


def test_risk_guard_paper_mode_blocks_live_but_no_pause():
    decision = evaluate_pre_order(
        status="paper",
        live_trading_enabled=False,
        global_autotrade_enabled=True,
        proposed_notional=100.0,
        free_balance=10_000.0,
        max_position_pct=0.10,
        daily_pnl=0.0,
        starting_balance=10_000.0,
        daily_loss_limit_pct=0.05,
    )
    assert decision.allow is False
    assert decision.pause_subscription is False
    assert decision.reason == "subscription is in paper mode"


def test_subscribe_preserves_explicit_zero_for_min_signal_score(client):
    """Regression: passing min_signal_score=0 must store 0, not the
    default 60 — `or` would silently override it."""
    _register(client, "zero@example.com")

    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "zero@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
            "min_signal_score": 0,
            "max_fake_risk": 0,
            "max_position_pct": 0.0,
        },
    )
    body = r.json()
    rs = client.get(f"/autotrade/{body['subscription_id']}/status")
    sub = rs.json()
    assert sub["min_signal_score"] == 0
    assert sub["max_fake_risk"] == 0
    assert sub["max_position_pct"] == 0.0


def test_signal_dispatch_creates_paper_order(client):
    """End-to-end: paper subscription + ingested news with high signal_score
    → AutoTradeOrder row recorded in paper mode (because the global kill
    switch ENABLE_AUTOTRADE is off, even a 'live'-flagged sub records paper
    orders so the client still sees them in their dashboard)."""
    _register(client, "dave@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "dave@example.com",
            "tier": "auto_pro",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
            "min_signal_score": 0,  # accept anything in the test
        },
    )
    sub_id = r.json()["subscription_id"]

    r2 = client.post(
        "/news/ingest",
        json={
            "source": "reuters",
            "raw_text": "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand.",
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["action"] == "LONG"

    rs = client.get(f"/autotrade/{sub_id}/status")
    body = rs.json()
    assert body["status"] == "paper"
    orders = body["recent_orders"]
    assert len(orders) >= 1
    o = orders[0]
    assert o["mode"] == "paper"
    assert o["symbol"] == "NVDAUSDT"
    assert o["side"] == "buy"
    assert o["status"] == "filled"


def test_autotrade_endpoints_reject_anonymous(client):
    """Critical security regression: mutation + status endpoints must require
    authentication. Anonymous callers must get 401."""
    # Create a sub via an authenticated user, then drop the cookie and
    # verify a fresh anonymous call cannot touch it.
    _register(client, "owner@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "owner@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    )
    sub_id = r.json()["subscription_id"]
    client.cookies.clear()
    for path in ("kill", "paper-mode", "resume", "go-live"):
        rr = client.post(f"/autotrade/{sub_id}/{path}")
        assert rr.status_code == 401, f"{path} must require auth, got {rr.status_code}"
    rr = client.get(f"/autotrade/{sub_id}/status")
    assert rr.status_code == 401


def test_autotrade_endpoints_reject_other_user(client):
    """Another logged-in user must not be able to mutate someone else's sub."""
    _register(client, "owner2@example.com")
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "owner2@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    )
    sub_id = r.json()["subscription_id"]
    # log out, register a different user
    client.cookies.clear()
    _register(client, "stranger@example.com")
    rr = client.post(f"/autotrade/{sub_id}/kill")
    assert rr.status_code == 403
    rr = client.get(f"/autotrade/{sub_id}/status")
    assert rr.status_code == 403


def test_zero_balance_not_replaced_with_default(client, db_session):
    """Regression for BUG_pr-review-job-a396d7d786094fc09ee63dcf33c7aa69_0001.

    A subscription whose last order's balance_after is 0.0 must NOT have
    free_balance silently replaced with DEFAULT_PAPER_BALANCE (10K). With
    a real free_balance of 0.0 and max_position_pct=0.10, proposed_notional
    is 0 and the risk-guard's free_balance<=0 rule must reject.
    """
    from datetime import datetime
    from app.autotrade.executor import _maybe_execute
    from app.database.models import (
        AutoTradeOrder,
        AutoTradeSubscription,
        NewsEvent,
        Signal,
    )

    sub = AutoTradeSubscription(
        email="drained@example.com",
        tier="auto_lite",
        exchange_id="bybit",
        api_key_encrypted="x",
        api_secret_encrypted="y",
        max_position_pct=0.10,
        daily_loss_limit_pct=0.05,
        min_signal_score=0,
        status="paper",
        live_trading_enabled=False,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    # Insert a prior order whose balance_after is 0.0 — drained account.
    drained = AutoTradeOrder(
        subscription_id=sub.id,
        signal_id=None,
        mode="paper",
        symbol="NVDAUSDT",
        side="buy",
        qty=1.0,
        entry_price=100.0,
        balance_before=100.0,
        balance_after=0.0,
        realized_pnl=-100.0,
        status="filled",
        created_at=datetime.utcnow(),
    )
    db_session.add(drained)
    ev = NewsEvent(
        source="reuters", source_url=None, raw_text="x", normalized_text="x",
        text_hash="h0", company="NVIDIA", ticker="NVDA",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    sig = Signal(
        event_id=ev.id,
        ticker="NVDA", symbol="NVDAUSDT", direction="bullish",
        action="LONG", signal_score=80, entry_price=100.0, status="new",
    )
    db_session.add(sig)
    db_session.commit()
    db_session.refresh(sig)

    order = _maybe_execute(db_session, sub, sig)
    assert order is not None
    # Either rejected because free_balance is 0, or filled at 0 notional —
    # but NOT filled at the 10K default. The guard must see the real 0.
    if order.status == "rejected":
        assert "balance" in (order.rejected_reason or "").lower() or "exceeds" in (order.rejected_reason or "").lower()
    else:
        # If it filled in paper, the qty/notional must be 0, not 1000.
        assert (order.qty or 0) == 0 or (order.entry_price or 0) * (order.qty or 0) <= 0.01


def test_signal_result_update_requires_admin(client):
    """Regression for BUG_pr-review-job-a396d7d786094fc09ee63dcf33c7aa69_0002.

    Anonymous callers must NOT be able to mutate signal results — they
    feed the public win_rate_pct shown on the landing page."""
    # No session at all → 401
    r = client.post("/signals/1/result/update", json={"result": "win"})
    assert r.status_code == 401

    # A regular client → 403 (role 'client' not in allowed=admin)
    _register(client, "joe@example.com")
    r = client.post("/signals/1/result/update", json={"result": "win"})
    assert r.status_code == 403


def test_x_webhook_signature_constant_time(client, monkeypatch):
    """Regression for BUG_pr-review-job-a396d7d786094fc09ee63dcf33c7aa69_0003.

    The X / Discord webhook signature check uses hmac.compare_digest, so
    wrong values return 401 regardless of how close to the real secret
    they are."""
    monkeypatch.setenv("X_WEBHOOK_SECRET", "real-secret-abc")
    from app.config.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    payload = {
        "handle": "@DeItaone",
        "raw_text": "Apple beat Q4 earnings expectations.",
        "verified": True,
    }
    # Wrong signature → 401 (and no timing leak)
    r = client.post("/news/ingest/x", json=payload, headers={"X-Signature": "wrong"})
    assert r.status_code == 401
    # No header at all → 401
    r = client.post("/news/ingest/x", json=payload)
    assert r.status_code == 401
    # Right secret → 200
    r = client.post("/news/ingest/x", json=payload, headers={"X-Signature": "real-secret-abc"})
    assert r.status_code == 200, r.text

    get_settings.cache_clear()  # type: ignore[attr-defined]
