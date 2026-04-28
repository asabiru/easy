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

    g = client.get(f"/support/ticket/{body['ticket_id']}")
    assert g.status_code == 200
    assert g.json()["category"] == "bug"


def test_create_ticket_invalid_message_too_short(client):
    r = client.post(
        "/support/ticket",
        json={"category": "other", "message": "hi"},
    )
    assert r.status_code == 422


def test_get_missing_ticket_404(client):
    r = client.get("/support/ticket/999999")
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
