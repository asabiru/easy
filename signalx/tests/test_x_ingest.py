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
        headers={"X-Webhook-Token": "wrong"},
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


def test_news_ingest_x_requires_news_ingest_secret(monkeypatch, client):
    """Regression for BUG_pr-review-job-5f8d54f0bce5493e86a1b963275ea000_0002.

    /news/ingest/x must honor NEWS_INGEST_SECRET like every other ingest
    endpoint — it dispatches the same autotrade orders."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "shared-key")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"handle": "DeItaone", "raw_text": "Acme reports earnings beat"}
    # No header → 401 (news_ingest_secret enforced)
    r = client.post("/news/ingest/x", json=payload)
    assert r.status_code == 401
    # Wrong → 401
    r = client.post("/news/ingest/x", json=payload, headers={"X-Signature": "wrong"})
    assert r.status_code == 401
    # Correct → not 401 (may 200 / may downstream error, just not auth)
    r = client.post("/news/ingest/x", json=payload, headers={"X-Signature": "shared-key"})
    assert r.status_code != 401


def test_news_ingest_x_with_both_secrets_set_to_different_values(monkeypatch, client):
    """Regression for BUG_pr-review-job-18f5b3c56eb1471986398db1336459e1_0001.

    When both NEWS_INGEST_SECRET and X_WEBHOOK_SECRET are configured to
    DIFFERENT values, the endpoint must still be reachable — the caller
    sends each secret in its own header (X-Signature for ingest,
    X-Webhook-Token for the X-only legacy secret)."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "alpha-secret")
    monkeypatch.setenv("X_WEBHOOK_SECRET", "beta-secret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {
        "handle": "@DeItaone",
        "raw_text": "Apple beat Q4 earnings expectations.",
        "verified": True,
    }
    # Both correct → 200
    r = client.post(
        "/news/ingest/x", json=payload,
        headers={"X-Signature": "alpha-secret", "X-Webhook-Token": "beta-secret"},
    )
    assert r.status_code == 200, r.text
    # Only X-Signature → 401 (X-Webhook-Token missing)
    r = client.post(
        "/news/ingest/x", json=payload, headers={"X-Signature": "alpha-secret"},
    )
    assert r.status_code == 401
    # Only X-Webhook-Token → 401 (X-Signature missing for ingest secret)
    r = client.post(
        "/news/ingest/x", json=payload, headers={"X-Webhook-Token": "beta-secret"},
    )
    assert r.status_code == 401
    # Swapped values in headers → 401
    r = client.post(
        "/news/ingest/x", json=payload,
        headers={"X-Signature": "beta-secret", "X-Webhook-Token": "alpha-secret"},
    )
    assert r.status_code == 401

    get_settings.cache_clear()
