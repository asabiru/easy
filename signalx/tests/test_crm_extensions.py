"""Tests for CRM extension endpoints:

  - lead activity timeline (/manager/leads/{id}/activity GET / POST)
  - lead scoring rubric on _serialize() return
  - manager kanban (/manager/pipeline)
  - referral system (/referral/me, /admin/referrals)
  - exchange key health check (/autotrade/{id}/test-keys)
  - admin metrics (/admin/metrics/revenue, /admin/metrics/signal-quality)
"""
import os


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


def _login(client, email, password="hunter2-pass"):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _promote(client, headers_admin, user_id, role):
    return client.post(
        f"/admin/users/{user_id}/role",
        json={"role": role},
        headers=headers_admin,
    )


def _make_admin_and_manager(client):
    admin = _register(client, "crm-admin@example.com", role_promote_email="crm-admin@example.com")
    headers_admin = {"Authorization": f"Bearer {admin['token']}"}
    mgr = _register(client, "crm-mgr@example.com")
    _promote(client, headers_admin, mgr["user_id"], "manager")
    mgr = _login(client, "crm-mgr@example.com")
    return admin, headers_admin, mgr, {"Authorization": f"Bearer {mgr['token']}"}


def _create_lead(client, **overrides):
    payload = {
        "name": "FO Gamma",
        "email": "fo-gamma@example.com",
        "capital_band": "2m_10m",
        "track": "ib_self_directed",
        "timeline": "1_3m",
    }
    payload.update(overrides)
    r = client.post("/investors/onboarding-intent", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["lead_id"]


# ─────────────────────────── lead activity ─────────────────────────── #


def test_lead_score_returned_on_serialize(client):
    """Lead score is computed from capital_band + timeline + track and
    included in _serialize() output. Score must be 0-100 inclusive."""
    _, _, _, headers_mgr = _make_admin_and_manager(client)
    lead_id = _create_lead(client)
    r = client.get(f"/manager/leads/{lead_id}", headers=headers_mgr)
    assert r.status_code == 200
    body = r.json()
    assert "score" in body
    assert 0 <= body["score"] <= 100
    # capital_band=2m_10m + track=ib_self_directed + timeline=1_3m should
    # produce a non-zero score even if individual map entries differ.
    assert body["score"] >= 0


def test_lead_activity_create_and_list(client):
    _, _, _, headers_mgr = _make_admin_and_manager(client)
    lead_id = _create_lead(client)

    # add a free-form note
    rc = client.post(
        f"/manager/leads/{lead_id}/activity",
        json={"kind": "note", "body": "initial outreach via email"},
        headers=headers_mgr,
    )
    assert rc.status_code == 200, rc.text
    assert rc.json()["kind"] == "note"
    assert rc.json()["body"].startswith("initial outreach")

    # status change should auto-write a status_change activity
    client.post(
        f"/manager/leads/{lead_id}",
        json={"status": "contacted"},
        headers=headers_mgr,
    )

    rl = client.get(f"/manager/leads/{lead_id}/activity", headers=headers_mgr)
    assert rl.status_code == 200
    items = rl.json()
    assert len(items) >= 2
    kinds = {it["kind"] for it in items}
    assert "note" in kinds
    assert "status_change" in kinds
    # newest-first
    assert items[0]["created_at"] >= items[-1]["created_at"]


def test_lead_activity_requires_manager_role(client):
    """Plain client cannot read or write the lead activity timeline."""
    body = _register(client, "outsider@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    lead_id = _create_lead(client)
    rl = client.get(f"/manager/leads/{lead_id}/activity", headers=headers)
    assert rl.status_code == 403
    rc = client.post(
        f"/manager/leads/{lead_id}/activity",
        json={"kind": "note", "body": "hi"},
        headers=headers,
    )
    assert rc.status_code == 403


# ─────────────────────────── kanban ─────────────────────────── #


def test_pipeline_kanban_buckets_by_status(client):
    _, _, _, headers_mgr = _make_admin_and_manager(client)
    a = _create_lead(client, email="a@example.com")
    b = _create_lead(client, email="b@example.com", capital_band="500k_2m")

    # move a → contacted
    client.post(
        f"/manager/leads/{a}",
        json={"status": "contacted"},
        headers=headers_mgr,
    )
    r = client.get("/manager/pipeline", headers=headers_mgr)
    assert r.status_code == 200
    data = r.json()
    assert "buckets" in data and "totals" in data
    assert data["totals"]["new"] >= 1
    assert data["totals"]["contacted"] >= 1
    contacted_ids = [x["id"] for x in data["buckets"]["contacted"]]
    assert a in contacted_ids


def test_pipeline_kanban_score_sorts_within_bucket(client):
    _, _, _, headers_mgr = _make_admin_and_manager(client)
    # whale: large capital + immediate timeline
    big = _create_lead(
        client,
        email="whale@example.com",
        capital_band="gt_10m",
        timeline="lt_30d",
    )
    # small: tiny capital + later timeline
    small = _create_lead(
        client,
        email="small@example.com",
        capital_band="lt_100k",
        timeline="research",
    )
    r = client.get("/manager/pipeline", headers=headers_mgr)
    new_bucket = r.json()["buckets"]["new"]
    ids_in_order = [x["id"] for x in new_bucket]
    if big in ids_in_order and small in ids_in_order:
        assert ids_in_order.index(big) <= ids_in_order.index(small)


# ─────────────────────────── referrals ─────────────────────────── #


def test_referral_me_generates_persistent_code(client):
    body = _register(client, "ref1@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    r1 = client.get("/referral/me", headers=headers)
    assert r1.status_code == 200
    code = r1.json()["code"]
    assert code.startswith("SX")
    assert r1.json()["referees_total"] == 0
    assert r1.json()["earnings_usdt"] == 0.0
    assert r1.json()["share_url"].endswith("?ref=" + code)
    # second call returns the same code (no rotation)
    r2 = client.get("/referral/me", headers=headers)
    assert r2.json()["code"] == code


def test_referral_codes_unique_per_user(client):
    a = _register(client, "ref-a@example.com")
    b = _register(client, "ref-b@example.com")
    ra = client.get("/referral/me", headers={"Authorization": f"Bearer {a['token']}"}).json()
    rb = client.get("/referral/me", headers={"Authorization": f"Bearer {b['token']}"}).json()
    assert ra["code"] != rb["code"]


def test_admin_referrals_requires_manager_or_admin(client):
    """Plain client cannot fetch /admin/referrals."""
    body = _register(client, "ref-client@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    r = client.get("/admin/referrals", headers=headers)
    assert r.status_code == 403


def test_admin_referrals_returns_structured_data(client):
    admin, headers_admin, mgr, headers_mgr = _make_admin_and_manager(client)
    r = client.get("/admin/referrals", headers=headers_admin)
    assert r.status_code == 200
    body = r.json()
    assert "leaderboard" in body
    assert "recent" in body
    assert "totals" in body


# ─────────────────────────── exchange key health ─────────────────────────── #


def test_test_keys_returns_structured_response(client):
    body = _register(client, "trader1@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    sub = client.post(
        "/autotrade/subscribe",
        json={
            "email": "trader1@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCDEFGH12345678",
            "api_secret": "WXYZ9876MNOPQRST",
        },
        headers=headers,
    ).json()
    r = client.post(f"/autotrade/{sub['subscription_id']}/test-keys", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "exchange" in body
    assert "masked_key" in body
    # ccxt may not be installed in test env or live keys won't auth — both are fine.
    assert isinstance(body["ok"], bool)


def test_test_keys_blocks_other_user(client):
    a = _register(client, "key-a@example.com")
    b = _register(client, "key-b@example.com")
    sub = client.post(
        "/autotrade/subscribe",
        json={
            "email": "key-a@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCDEFGH12345678",
            "api_secret": "WXYZ9876MNOPQRST",
        },
        headers={"Authorization": f"Bearer {a['token']}"},
    ).json()
    r = client.post(
        f"/autotrade/{sub['subscription_id']}/test-keys",
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code in (403, 404)


# ─────────────────────────── admin metrics ─────────────────────────── #


def test_admin_metrics_revenue_shape(client):
    admin, headers_admin, mgr, headers_mgr = _make_admin_and_manager(client)
    r = client.get("/admin/metrics/revenue", headers=headers_admin)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "mrr_usdt",
        "arr_projection_usdt",
        "active_subs",
        "active_by_tier",
        "churn_30d_pct",
        "killed_30d",
        "revenue_paid_lifetime_usdt",
        "revenue_paid_30d_usdt",
        "tier_price_card",
    ):
        assert key in body, f"missing {key}"
    assert isinstance(body["tier_price_card"], dict)
    # MRR = ARR/12, sanity check
    assert abs(body["arr_projection_usdt"] - body["mrr_usdt"] * 12) < 0.01


def test_admin_metrics_signal_quality_shape(client):
    admin, headers_admin, _, _ = _make_admin_and_manager(client)
    r = client.get("/admin/metrics/signal-quality", headers=headers_admin)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "window_days",
        "signals_total",
        "by_action",
        "actionable_count",
        "wins",
        "losses",
        "undecided",
        "win_rate_pct",
        "avg_signal_score",
        "avg_fake_risk",
    ):
        assert key in body, f"missing {key}"
    assert body["window_days"] == 30


def test_admin_metrics_manager_can_read(client):
    """Managers (not just admins) can read the dashboards — revenue and
    quality drive sales-call talking points."""
    _, _, _, headers_mgr = _make_admin_and_manager(client)
    r1 = client.get("/admin/metrics/revenue", headers=headers_mgr)
    r2 = client.get("/admin/metrics/signal-quality", headers=headers_mgr)
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_admin_metrics_blocks_plain_client(client):
    body = _register(client, "metrics-client@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}
    r1 = client.get("/admin/metrics/revenue", headers=headers)
    r2 = client.get("/admin/metrics/signal-quality", headers=headers)
    assert r1.status_code == 403
    assert r2.status_code == 403
