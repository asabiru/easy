"""Auth, role-gating, admin/manager/client panel coverage."""
import os


def _register(client, email, password="hunter2-pass", role_promote_email=None):
    if role_promote_email:
        os.environ["BOOTSTRAP_ADMIN_EMAIL"] = role_promote_email
        from app.config.settings import get_settings
        get_settings.cache_clear()  # reload settings
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


def test_register_login_me(client):
    body = _register(client, "alice@example.com")
    assert body["role"] == "client"
    assert body["token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_login_with_wrong_password_fails(client):
    _register(client, "bob@example.com")
    r = client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong"})
    assert r.status_code == 401


def test_admin_endpoints_require_role(client):
    body = _register(client, "client1@example.com")
    r = client.get("/admin/system", headers={"Authorization": f"Bearer {body['token']}"})
    assert r.status_code == 403


def test_admin_can_promote_user_and_force_kill(client):
    # bootstrap an admin
    admin = _register(client, "admin@example.com", role_promote_email="admin@example.com")
    assert admin["role"] == "admin"
    headers_admin = {"Authorization": f"Bearer {admin['token']}"}

    # create a regular user
    user = _register(client, "promo@example.com")

    # admin promotes them to manager
    rp = client.post(
        f"/admin/users/{user['user_id']}/role",
        json={"role": "manager"},
        headers=headers_admin,
    )
    assert rp.status_code == 200
    assert rp.json()["role"] == "manager"

    # admin sees system overview
    rs = client.get("/admin/system", headers=headers_admin)
    assert rs.status_code == 200
    assert "users_count" in rs.json()

    # admin can force-kill a sub
    sub = client.post(
        "/autotrade/subscribe",
        json={
            "email": "to-kill@example.com",
            "tier": "auto_pro",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
    ).json()
    rk = client.post(f"/admin/subscriptions/{sub['subscription_id']}/force-kill", headers=headers_admin)
    assert rk.status_code == 200
    assert rk.json()["status"] == "killed"


def test_manager_lead_pipeline(client):
    admin = _register(client, "admin2@example.com", role_promote_email="admin2@example.com")
    headers_admin = {"Authorization": f"Bearer {admin['token']}"}
    mgr = _register(client, "mgr@example.com")
    client.post(
        f"/admin/users/{mgr['user_id']}/role",
        json={"role": "manager"},
        headers=headers_admin,
    )
    # re-login to refresh token role claim
    mgr = _login(client, "mgr@example.com")
    headers_mgr = {"Authorization": f"Bearer {mgr['token']}"}

    # create lead via public endpoint
    r = client.post(
        "/investors/onboarding-intent",
        json={
            "name": "FO Beta",
            "email": "fo@example.com",
            "capital_band": "500k_2m",
            "track": "ib_self_directed",
        },
    )
    lead_id = r.json()["lead_id"]

    # manager lists + updates
    rl = client.get("/manager/leads", headers=headers_mgr)
    assert rl.status_code == 200
    assert any(x["id"] == lead_id for x in rl.json())

    ru = client.post(
        f"/manager/leads/{lead_id}",
        json={"status": "contacted", "note_append": "called the GP", "assign_to_me": True},
        headers=headers_mgr,
    )
    assert ru.status_code == 200
    body = ru.json()
    assert body["status"] == "contacted"
    assert body["assigned_manager_id"] == mgr["user_id"]
    assert "called the GP" in (body["notes"] or "")


def test_client_only_sees_own_subscriptions(client):
    body = _register(client, "owner@example.com")
    headers = {"Authorization": f"Bearer {body['token']}"}

    # subscribe linked to authed user (cookie-based auth via bearer here)
    r = client.post(
        "/autotrade/subscribe",
        json={
            "email": "owner@example.com",
            "tier": "auto_lite",
            "exchange_id": "bybit",
            "api_key": "ABCD1234EFGH",
            "api_secret": "WXYZ9876MNOP",
        },
        headers=headers,
    )
    sub_id = r.json()["subscription_id"]

    rs = client.get("/client/me/subscriptions", headers=headers)
    assert rs.status_code == 200
    assert any(s["id"] == sub_id for s in rs.json())

    # detail endpoint
    rd = client.get(f"/client/me/subscriptions/{sub_id}", headers=headers)
    assert rd.status_code == 200
    assert rd.json()["id"] == sub_id

    # other user cannot read it
    other = _register(client, "other@example.com")
    headers_other = {"Authorization": f"Bearer {other['token']}"}
    rx = client.get(f"/client/me/subscriptions/{sub_id}", headers=headers_other)
    assert rx.status_code in (403, 404)
