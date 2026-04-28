"""Tests for parallel agent extensions:

  - Anti-Fake: tier-based fake_risk gating on /signals + /signals/filter-stats
  - Trading-Risk: win_rate_alert flag on /admin/metrics/signal-quality
  - Security: rate-limit module enable/disable + headers
  - Compliance: /legal/disclosures.html static page is served
"""
import os

from datetime import datetime, timedelta


def _register(client, email, password="hunter2-pass", role_promote_email=None):
    if role_promote_email:
        os.environ["BOOTSTRAP_ADMIN_EMAIL"] = role_promote_email
        from app.config.settings import get_settings

        get_settings.cache_clear()
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": email.split("@")[0]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _make_event(db, idx: str, fake_risk: float):
    from app.database.models import NewsEvent

    ev = NewsEvent(
        source="test",
        source_url=f"https://example.test/{idx}",
        raw_text=f"Synthetic event {idx}",
        normalized_text=f"synthetic event {idx}",
        text_hash=f"h-{idx}",
        ticker="NVDA",
        event_type="rumor",
        direction="bullish",
        confidence=0.7,
        impact_score=0.5,
        urgency="normal",
        fake_risk=fake_risk,
    )
    db.add(ev)
    db.flush()
    return ev


def _seed_signals(db, *, n_clean=3, n_borderline=2, n_dirty=2):
    """Insert synthetic Signal+NewsEvent rows for /signals filter tests.

    fake_risk lives on NewsEvent — Signal joins through `event_id`. Each
    bucket has a distinct fake_risk band so we can assert the tier cap
    behaviour deterministically.
    """
    from app.database.models import NewsEvent, Signal

    # fake_risk is on the 0–100 scale (see app/analysis/fake_risk.py).
    for i in range(n_clean):
        _make_event(db, f"clean-{i}", 10.0)
    for i in range(n_borderline):
        _make_event(db, f"mid-{i}", 45.0)
    for i in range(n_dirty):
        _make_event(db, f"dirty-{i}", 85.0)

    for ev in db.query(NewsEvent).all():
        s = Signal(
            event_id=ev.id,
            ticker="NVDA",
            symbol="NVDAUSDT",
            direction="LONG",
            action="LONG" if ev.fake_risk < 50 else "WATCH",
            signal_score=70 if ev.fake_risk < 50 else 40,
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=104.0,
            max_holding_minutes=30,
            risk_level="medium",
            reason="test",
            status="published",
        )
        db.add(s)
    db.commit()


# ─────────────────────────── Anti-Fake gate ─────────────────────────── #


def test_signals_anonymous_caller_filtered_to_clean(client_with_db):
    cl, db = client_with_db
    _seed_signals(db)
    r = cl.get("/signals")
    assert r.status_code == 200
    body = r.json()
    # Anonymous tier cap is 30 — only the 3 clean signals (fake_risk=10) pass.
    assert all(s["fake_risk"] <= 30 for s in body)
    assert len(body) == 3


def test_signals_admin_sees_all(client_with_db):
    cl, db = client_with_db
    _seed_signals(db)
    admin = _register(cl, "ag-admin@example.com", role_promote_email="ag-admin@example.com")
    r = cl.get("/signals", headers={"Authorization": f"Bearer {admin['token']}"})
    assert r.status_code == 200
    # admin → vip cap (1.0) → sees clean + borderline + dirty
    assert len(r.json()) == 7


def test_filter_stats_reports_drop_count(client_with_db):
    cl, db = client_with_db
    _seed_signals(db)
    r = cl.get("/signals/filter-stats?window_hours=24")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "anonymous"
    assert body["fake_risk_cap"] == 30
    assert body["signals_total"] == 7
    assert body["signals_visible"] == 3
    assert body["signals_filtered"] == 4
    assert body["filtered_pct"] > 50.0


# ─────────────────────────── Trading-Risk alert ─────────────────────────── #


def test_signal_quality_win_rate_alert_inactive_when_no_data(client):
    admin = _register(client, "tr-admin@example.com", role_promote_email="tr-admin@example.com")
    r = client.get(
        "/admin/metrics/signal-quality",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "win_rate_alert" in body
    # No signals → no decided → no alert.
    assert body["win_rate_alert"] is False
    assert body["win_rate_7d_decided"] == 0


# ─────────────────────────── Security: rate limiter ─────────────────────────── #


def test_rate_limiter_disabled_in_tests(client):
    """Sanity check that the conftest disables rate limiting — otherwise
    other tests in the suite would intermittently 429 when re-running
    /referral/me in tight loops."""
    body = _register(client, "rl@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    for _ in range(40):
        r = client.get("/referral/me", headers=headers)
        assert r.status_code == 200, r.text


def test_rate_limiter_module_constructs_correctly():
    """The limiter is a process-singleton; reaching the cap triggers 429
    with a Retry-After header."""
    import os

    os.environ["RATE_LIMIT_ENABLED"] = "true"
    try:
        from app.security.rate_limit import RateLimiter
        from fastapi import HTTPException

        rl = RateLimiter("test_local", per_ip_per_min=2)

        class _Req:
            class _C:
                host = "1.2.3.4"
            client = _C()
            headers: dict[str, str] = {}

        req = _Req()
        rl(req)  # 1
        rl(req)  # 2
        try:
            rl(req)  # 3 → should raise
            raise AssertionError("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 429
            assert "Retry-After" in exc.headers
    finally:
        os.environ["RATE_LIMIT_ENABLED"] = "false"


# ─────────────────────────── Compliance: legal page ─────────────────────────── #


def test_legal_disclosures_page_is_served(client):
    r = client.get("/legal/disclosures.html")
    assert r.status_code == 200
    assert "Risk disclosures" in r.text
    # Must mention the founder qualification disclaimer verbatim.
    assert "personal qualification" in r.text
    assert "OFAC" in r.text


def test_landing_page_links_to_legal(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "/legal/disclosures.html" in r.text
    # Anti-pump rail must be present so marketing copy renders.
    assert "anti-pump-rail" in r.text or "Signals filtered" in r.text
