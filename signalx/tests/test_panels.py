

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
