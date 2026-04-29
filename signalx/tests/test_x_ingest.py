"""Integration tests for /news/ingest/x — the X (Twitter) webhook ingest."""
import hashlib
import hmac as _hmac
import json


def _sign(payload: dict, secret: str) -> tuple[bytes, str]:
    """Serialize the payload and compute the HMAC-SHA256 signature the
    way /news/ingest verifies it. Returns (raw body bytes, hex digest).
    Tests that want to exercise the authenticated path send ``content=body``
    (not ``json=payload``) so the exact bytes we signed reach the server.
    """
    body = json.dumps(payload).encode()
    sig = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


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


def test_news_ingest_503_in_prod_when_secret_unset(monkeypatch, client):
    """Regression for BUG_pr-review-job-9e08d504df2d48139d2f1508f4d6eaa1_0002.

    In non-dev environments (prod/staging), an unset NEWS_INGEST_SECRET
    MUST refuse the endpoint rather than silently pass-through. An
    attacker could otherwise POST fake news into a default-config
    deploy and dispatch autotrade orders into every live subscription.
    """
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("NEWS_INGEST_SECRET", raising=False)
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"source": "reuters", "raw_text": "Acme reports earnings beat"}
    r = client.post("/news/ingest", json=payload)
    assert r.status_code == 503
    assert "misconfigured" in r.json().get("detail", "")


def test_news_ingest_allows_unset_secret_in_dev(monkeypatch, client):
    """In dev (APP_ENV=dev, conftest default), unset secret = silent
    pass so existing ingest tests don't need header plumbing."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("NEWS_INGEST_SECRET", raising=False)
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"source": "reuters", "raw_text": "Acme reports earnings beat"}
    r = client.post("/news/ingest", json=payload)
    assert r.status_code != 503


def test_news_ingest_requires_signature_when_secret_set(monkeypatch, client):
    """Generic /news/ingest dispatches autotrade orders, must be protected
    when news_ingest_secret is configured. Post-fix the header must be an
    HMAC-SHA256 of the body keyed on the secret, not the raw secret itself
    (BUG_pr-review-job-76af88f3b2924077813350cc4cc47bef_0002)."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"source": "reuters", "raw_text": "Acme reports earnings beat"}
    body, sig = _sign(payload, "topsecret")
    # No header → 401
    r = client.post("/news/ingest", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    # Wrong header → 401
    r = client.post(
        "/news/ingest", content=body,
        headers={"Content-Type": "application/json", "X-Signature": "wrong"},
    )
    assert r.status_code == 401
    # Raw secret in header (old style) → 401 post-fix
    r = client.post(
        "/news/ingest", content=body,
        headers={"Content-Type": "application/json", "X-Signature": "topsecret"},
    )
    assert r.status_code == 401
    # Proper HMAC → not 401
    r = client.post(
        "/news/ingest", content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code != 401


def test_news_ingest_hmac_signature_is_body_bound(monkeypatch, client):
    """Regression for BUG_pr-review-job-76af88f3b2924077813350cc4cc47bef_0002.

    The signature is HMAC(body, secret) — a signature computed for
    payload A must NOT validate a request with payload B, even if both
    share the same secret. Captured signatures are therefore useless
    for forging arbitrary payloads."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload_a = {"source": "reuters", "raw_text": "AAPL beat Q4 expectations."}
    payload_b = {"source": "reuters", "raw_text": "FAKE TIP NVDA acquisition imminent."}
    body_a, sig_a = _sign(payload_a, "topsecret")
    body_b, _ = _sign(payload_b, "topsecret")
    # Replaying signature from A against body B must 401.
    r = client.post(
        "/news/ingest", content=body_b,
        headers={"Content-Type": "application/json", "X-Signature": sig_a},
    )
    assert r.status_code == 401
    # A's own signature still verifies A.
    r = client.post(
        "/news/ingest", content=body_a,
        headers={"Content-Type": "application/json", "X-Signature": sig_a},
    )
    assert r.status_code != 401


def test_news_ingest_discord_requires_signature_when_secret_set(monkeypatch, client):
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"channel": "alpha", "raw_text": "NVDA up bid", "author": "trader"}
    body, sig = _sign(payload, "topsecret")
    r = client.post(
        "/news/ingest/discord", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
    r = client.post(
        "/news/ingest/discord", content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code != 401


def test_news_ingest_rss_requires_signature_when_secret_set(monkeypatch, client):
    monkeypatch.setenv("NEWS_INGEST_SECRET", "topsecret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"feed_id": "reuters_business", "raw_text": "NVDA up bid"}
    body, sig = _sign(payload, "topsecret")
    r = client.post(
        "/news/ingest/rss", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
    r = client.post(
        "/news/ingest/rss", content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code != 401


def test_news_ingest_x_requires_news_ingest_secret(monkeypatch, client):
    """/news/ingest/x must honor NEWS_INGEST_SECRET like every other ingest
    endpoint — it dispatches the same autotrade orders."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "shared-key")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {"handle": "DeItaone", "raw_text": "Acme reports earnings beat"}
    body, sig = _sign(payload, "shared-key")
    # No header → 401
    r = client.post(
        "/news/ingest/x", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
    # Wrong signature → 401
    r = client.post(
        "/news/ingest/x", content=body,
        headers={"Content-Type": "application/json", "X-Signature": "wrong"},
    )
    assert r.status_code == 401
    # Proper HMAC → not 401 (may be 200 / may downstream error, just not auth)
    r = client.post(
        "/news/ingest/x", content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code != 401


def test_news_ingest_x_with_both_secrets_set_to_different_values(monkeypatch, client):
    """When both NEWS_INGEST_SECRET and X_WEBHOOK_SECRET are configured
    to DIFFERENT values, the endpoint must still be reachable — the
    caller sends a body-bound HMAC in X-Signature plus the X-only legacy
    secret in X-Webhook-Token."""
    monkeypatch.setenv("NEWS_INGEST_SECRET", "alpha-secret")
    monkeypatch.setenv("X_WEBHOOK_SECRET", "beta-secret")
    from app.config.settings import get_settings
    get_settings.cache_clear()

    payload = {
        "handle": "@DeItaone",
        "raw_text": "Apple beat Q4 earnings expectations.",
        "verified": True,
    }
    body, sig = _sign(payload, "alpha-secret")
    _, webhook_sig = _sign(payload, "beta-secret")

    # Both correct → 200
    r = client.post(
        "/news/ingest/x", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig,
            "X-Webhook-Token": webhook_sig,
        },
    )
    assert r.status_code == 200, r.text
    # Only X-Signature → 401 (X-Webhook-Token missing)
    r = client.post(
        "/news/ingest/x", content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code == 401
    # Only X-Webhook-Token → 401 (X-Signature missing)
    r = client.post(
        "/news/ingest/x", content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Token": webhook_sig},
    )
    assert r.status_code == 401
    # Raw secret in X-Webhook-Token (old style) → 401 post-fix
    r = client.post(
        "/news/ingest/x", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig,
            "X-Webhook-Token": "beta-secret",
        },
    )
    assert r.status_code == 401

    get_settings.cache_clear()
