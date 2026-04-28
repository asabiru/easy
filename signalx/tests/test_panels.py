

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

    /autotrade/subscribe now requires auth (anonymous subscribe was a
    real security bug — see BUG_pr-review-job-e1afda66785e4b3fb669e417dec58b5c_0001).
    The remaining email-case invariant is that a logged-in user posting
    a mixed-case email still ends up with a lowercased email on the
    subscription row, so downstream email-based ownership fallback
    (used for legacy rows where user_id is NULL) keeps working
    case-insensitively against routes_auth's lowercased registration.
    """
    from app.database.models import AutoTradeSubscription

    # Register (auth lowercases the email) and log in.
    r = client.post("/auth/register", json={"email": "Alice@Example.com", "password": "pw1234567"})
    assert r.status_code == 200
    r = client.post("/auth/login", json={"email": "alice@example.com", "password": "pw1234567"})
    assert r.status_code == 200

    # Authenticated subscribe with mixed-case email payload.
    r = client.post("/autotrade/subscribe", json={
        "email": "Alice@Example.com",
        "tier": "auto_lite",
        "exchange_id": "bybit",
        "api_key": "k" * 16,
        "api_secret": "s" * 16,
    })
    assert r.status_code == 200, r.text
    sub_id = r.json()["subscription_id"]

    # The stored email should be lowercased.
    sub = (
        db_session.query(AutoTradeSubscription)
        .filter(AutoTradeSubscription.id == sub_id)
        .first()
    )
    assert sub.email == "alice@example.com"
    # And the subscription must be linked to the user_id (primary path).
    assert sub.user_id is not None

    # /client/me/subscriptions must list it and detail must be readable.
    r = client.get("/client/me/subscriptions")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert sub_id in ids
    r = client.get(f"/client/me/subscriptions/{sub_id}")
    assert r.status_code == 200


def test_email_match_is_only_for_anonymous_subscriptions(client_with_db):
    """Regression for BUG_pr-review-job-2c2ebe1fc6814cffacdc1da6619c82de_0002.

    User A subscribes (logged in). User B (with whatever email) must NOT
    see / read User A's subscription detail just because some email field
    coincidentally matches."""
    from app.database.models import AutoTradeSubscription, User
    from app.auth.security import hash_password

    client, db = client_with_db
    # Two separate users
    a = User(email="alice@example.com", password_hash=hash_password("p"), role="client", is_active=True)
    b = User(email="bob@example.com", password_hash=hash_password("p"), role="client", is_active=True)
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    # Alice subscribes (her email gets stored, user_id set to a.id)
    sub = AutoTradeSubscription(
        email="alice@example.com",
        user_id=a.id,
        tier="auto_lite",
        exchange_id="bybit",
        api_key_encrypted="x" * 16,
        api_secret_encrypted="x" * 16,
        status="paper",
        live_trading_enabled=False,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    # Now Bob logs in
    r = client.post("/auth/login", json={"email": "bob@example.com", "password": "p"})
    assert r.status_code == 200

    # Bob attempts to read Alice's subscription detail → 403
    r = client.get(f"/client/me/subscriptions/{sub.id}")
    assert r.status_code == 403

    # Bob's listing must NOT include Alice's subscription
    r = client.get("/client/me/subscriptions")
    assert r.status_code == 200
    assert sub.id not in [s["id"] for s in r.json()]


def test_admin_signals_serializes_impact_and_confidence_from_news_event(client_with_db):
    """Regression for BUG_pr-review-job-f77e2282cf15490e8165842c71a28937_0001.

    Signal model has no impact_score / confidence columns — those live on
    NewsEvent. The admin endpoint must join through event_id and not 500."""
    from app.database.models import NewsEvent, Signal, User
    from app.auth.security import hash_password

    client, db = client_with_db
    db.add(User(email="ad@example.com", password_hash=hash_password("p"),
                role="admin", is_active=True))
    db.commit()
    r = client.post("/auth/login", json={"email": "ad@example.com", "password": "p"})
    assert r.status_code == 200

    ev = NewsEvent(
        source="reuters", source_url=None, raw_text="x", normalized_text="x",
        text_hash="hsig", company="NVIDIA", ticker="NVDA",
        impact_score=0.7, confidence=0.9,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    sig_with_event = Signal(
        event_id=ev.id, ticker="NVDA", symbol="NVDAUSDT", direction="bullish",
        action="LONG", signal_score=80, status="new",
    )
    # Second signal points at a NewsEvent with NULL impact / confidence to
    # exercise the join's nullable side without violating the NOT NULL
    # constraint on Signal.event_id.
    ev2 = NewsEvent(
        source="reuters", source_url=None, raw_text="y", normalized_text="y",
        text_hash="hsig2", company="Tesla", ticker="TSLA",
        impact_score=None, confidence=None,
    )
    db.add(ev2)
    db.commit()
    db.refresh(ev2)
    sig_no_metrics = Signal(
        event_id=ev2.id, ticker="TSLA", symbol="TSLAUSDT", direction="bullish",
        action="WATCH", signal_score=40, status="new",
    )
    db.add_all([sig_with_event, sig_no_metrics])
    db.commit()

    r = client.get("/admin/signals")
    assert r.status_code == 200, r.text
    body = r.json()
    by_ticker = {s["ticker"]: s for s in body}
    assert by_ticker["NVDA"]["impact_score"] == 0.7
    assert by_ticker["NVDA"]["confidence"] == 0.9
    assert by_ticker["TSLA"]["impact_score"] is None
    assert by_ticker["TSLA"]["confidence"] is None
