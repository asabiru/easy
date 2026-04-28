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


def test_news_ingest_requires_signature_when_secret_set(monkeypatch, client):
    """Regression for BUG_pr-review-job-2c2ebe1fc6814cffacdc1da6619c82de_0003.

    Generic /news/ingest dispatches autotrade orders, must be protected
    when news_ingest_secret is configured."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"source": "reuters", "raw_text": "Acme reports earnings beat"}
    # No header → 401
    r = client.post("/news/ingest", json=payload)
    assert r.status_code == 401
    # Wrong header → 401
    r = client.post("/news/ingest", json=payload, headers={"X-Signature": "wrong"})
    assert r.status_code == 401
    # Right header → not 401
    r = client.post("/news/ingest", json=payload, headers={"X-Signature": "topsecret"})
    assert r.status_code != 401


def test_news_ingest_discord_requires_signature_when_secret_set(monkeypatch, client):
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"channel": "alpha", "raw_text": "NVDA up bid", "author": "trader"}
    r = client.post("/news/ingest/discord", json=payload)
    assert r.status_code == 401
    r = client.post("/news/ingest/discord", json=payload, headers={"X-Signature": "topsecret"})
    assert r.status_code != 401


def test_news_ingest_rss_requires_signature_when_secret_set(monkeypatch, client):
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"feed_id": "reuters_business", "raw_text": "NVDA up bid"}
    r = client.post("/news/ingest/rss", json=payload)
    assert r.status_code == 401
    r = client.post("/news/ingest/rss", json=payload, headers={"X-Signature": "topsecret"})
    assert r.status_code != 401
