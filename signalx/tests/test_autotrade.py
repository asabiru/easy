"""Auto-trade subscription + executor tests.

Covers: encryption round-trip, paper-mode subscribe flow, kill switch,
risk-guard rejection (oversized position, daily loss), end-to-end signal
dispatch in paper mode through /news/ingest.
"""
from app.autotrade.crypto import decrypt, encrypt
from app.autotrade.risk_guard import evaluate_pre_order


def test_encrypt_round_trip():
    cipher = encrypt("super-secret-api-key")
    assert cipher and cipher != "super-secret-api-key"
    assert decrypt(cipher) == "super-secret-api-key"


def test_encrypt_empty():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_subscribe_flow_starts_in_paper_mode(client):
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


def test_signal_dispatch_creates_paper_order(client):
    """End-to-end: paper subscription + ingested news with high signal_score
    → AutoTradeOrder row recorded in paper mode (because the global kill
    switch ENABLE_AUTOTRADE is off, even a 'live'-flagged sub records paper
    orders so the client still sees them in their dashboard)."""
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
