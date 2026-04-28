

def test_manager_lead_update_writes_audit_log(client_with_db):
    client, db_session = client_with_db
    """Regression for BUG_pr-review-job-9f12362cc0a345f98b51e829bdd1d90f_0003.

    Manager mutations of investor leads MUST write an AuditLog row, like
    every other admin/manager mutation in the codebase."""
    import json
    from app.database.models import AuditLog, InvestorLead, User
    from app.auth.security import hash_password

    # Seed a manager + a lead via the test DB session (session is shared
    # with the TestClient via dependency override).
    mgr = User(email="mgr@example.com", password_hash=hash_password("p"), role="manager", is_active=True)
    db_session.add(mgr)
    lead = InvestorLead(name="Acme Capital", email="hello@acme.com", capital_band="500k_1m", status="new")
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(mgr)
    db_session.refresh(lead)

    # Log in as the manager to get a session cookie
    r = client.post("/auth/login", json={"email": "mgr@example.com", "password": "p"})
    assert r.status_code == 200, r.text

    r = client.post(f"/manager/leads/{lead.id}", json={"status": "qualified", "assign_to_me": True})
    assert r.status_code == 200

    # AuditLog row must exist
    audits = db_session.query(AuditLog).filter(AuditLog.action == "lead.update").all()
    assert len(audits) == 1
    a = audits[0]
    assert a.target_type == "investor_lead"
    assert a.target_id == lead.id
    assert a.actor_user_id == mgr.id
    body = json.loads(a.payload)
    assert body["status"] == {"from": "new", "to": "qualified"}


def test_subscription_email_lowercased_for_case_insensitive_ownership(client_with_db):
    client, db_session = client_with_db
    """Regression for BUG_pr-review-job-c8f76a0a7ea14148af0c2e1080f31a5f_0001/0002/0003.

    A subscription created anonymously with mixed-case email must still
    be visible and own-able by a user whose account email is the
    lowercased version (routes_auth always lowercases on register)."""
    from app.database.models import AutoTradeSubscription
    from app.auth.security import hash_password
    from app.database.models import User

    # Anonymous (no auth) subscribe with mixed-case email
    r = client.post("/autotrade/subscribe", json={
        "email": "Alice@Example.com",
        "tier": "auto_lite",
        "exchange_id": "bybit",
        "api_key": "k" * 16,
        "api_secret": "s" * 16,
    })
    assert r.status_code == 200, r.text
    sub_id = r.json()["subscription_id"]

    # The stored email should be lowercased
    sub = db_session.query(AutoTradeSubscription).filter(AutoTradeSubscription.id == sub_id).first()
    assert sub.email == "alice@example.com"

    # Now register Alice (auth always lowercases) and log in
    r = client.post("/auth/register", json={"email": "Alice@Example.com", "password": "pw1234567"})
    assert r.status_code == 200
    r = client.post("/auth/login", json={"email": "alice@example.com", "password": "pw1234567"})
    assert r.status_code == 200

    # /client/me/subscriptions must list the anonymous-subscribe row
    r = client.get("/client/me/subscriptions")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert sub_id in ids

    # /client/me/subscriptions/{id} must succeed (not 403)
    r = client.get(f"/client/me/subscriptions/{sub_id}")
    assert r.status_code == 200
