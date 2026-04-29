"""Support endpoint + Telegram bot command surface tests."""


def test_create_support_ticket(client):
    r = client.post(
        "/support/ticket",
        json={
            "email": "user@example.com",
            "category": "bug",
            "message": "I never received the latest NVDA signal in Telegram.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["ticket_id"], int)
    assert body["status"] == "open"
    # Capability token for anonymous read-back. Must be unguessable
    # (>=16 chars) — see BUG_pr-review-job-76af88f3b2924077813350cc4cc47bef_0001.
    assert isinstance(body["ticket_token"], str) and len(body["ticket_token"]) >= 20

    g = client.get(f"/support/ticket/{body['ticket_token']}")
    assert g.status_code == 200
    assert g.json()["category"] == "bug"


def test_create_ticket_invalid_message_too_short(client):
    r = client.post(
        "/support/ticket",
        json={"category": "other", "message": "hi"},
    )
    assert r.status_code == 422


def test_get_ticket_by_sequential_id_is_rejected(client):
    """Regression for BUG_pr-review-job-76af88f3b2924077813350cc4cc47bef_0001.

    The old endpoint exposed tickets by their sequential int id, so an
    attacker could enumerate all submitters' emails and messages by
    incrementing the id. The fix looks up by a URL-safe random token
    instead, and short inputs (int ids) 404 without database lookup.
    """
    r = client.post(
        "/support/ticket",
        json={"email": "victim@example.com", "category": "billing", "message": "leak me if you can"},
    )
    assert r.status_code == 200
    # Int-id path that used to work — must 404 now, no matter how small.
    for candidate in ("1", "2", "999999"):
        g = client.get(f"/support/ticket/{candidate}")
        assert g.status_code == 404, f"sequential id {candidate} leaked ticket"


def test_get_missing_ticket_token_404(client):
    # A well-formed-looking but wrong token — must 404.
    r = client.get("/support/ticket/thisisnottherealcapabilitytoken1234")
    assert r.status_code == 404


def test_bot_help(client):
    r = client.get("/bot/help")
    assert r.status_code == 200
    cmds = [c["cmd"] for c in r.json()["commands"]]
    assert "/help" in cmds
    assert "/status" in cmds
    assert "/last [TICKER]" in cmds
    assert "/about" in cmds


def test_bot_status_returns_signal_count(client):
    r = client.get("/bot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "signals_last_hour" in body


def test_bot_about_includes_disclaimer(client):
    r = client.get("/bot/about")
    assert r.status_code == 200
    body = r.json()
    assert "research-only" in body["disclaimer"].lower()
    assert body["version"].startswith("0.")


def test_bot_last_filters_by_ticker(client):
    # Seed via news ingest
    client.post(
        "/news/ingest",
        json={
            "source": "reuters",
            "raw_text": "Nvidia raises Q2 revenue guidance above expectations.",
        },
    )
    r = client.get("/bot/last?ticker=NVDA&limit=3")
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(i["ticker"] == "NVDA" for i in items)
