"""Integration tests for /news/ingest/x — the X (Twitter) webhook ingest."""


def test_x_webhook_known_press_handle_yields_signal(client):
    r = client.post(
        "/news/ingest/x",
        json={
            "handle": "Reuters",
            "raw_text": "Nvidia raises Q2 revenue guidance above Wall Street expectations.",
            "tweet_url": "https://x.com/Reuters/status/1",
            "verified": True,
            "followers": 25_000_000,
            "account_age_days": 5_000,
            "has_authoritative_link": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company"] == "Nvidia"
    assert body["ticker"] == "NVDA"
    assert body["action"] in ("LONG", "WATCH")
    assert isinstance(body["fake_risk"], int)
    assert body["fake_risk"] < 50


def test_x_webhook_anon_rumour_high_fake_risk_skips(client):
    r = client.post(
        "/news/ingest/x",
        json={
            "handle": "obviously_fake_acct",
            "raw_text": "BREAKING insider tip: Nvidia about to be acquired, 100x easy.",
            "tweet_url": "https://x.com/obviously_fake_acct/status/2",
            "verified": False,
            "followers": 50,
            "account_age_days": 5,
            "has_authoritative_link": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fake_risk"] >= 50, body
    assert body["action"] == "SKIP"


def test_x_webhook_signature_mismatch_rejected(client, monkeypatch):
    from app.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "x_webhook_secret", "expected-secret")
    r = client.post(
        "/news/ingest/x",
        json={
            "handle": "DeItaone",
            "raw_text": "ConocoPhillips beats earnings per share estimates.",
        },
        headers={"X-Signature": "wrong"},
    )
    assert r.status_code == 401
