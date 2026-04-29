"""Investor onboarding endpoint."""


def test_investor_intent_creates_ticket(client):
    r = client.post(
        "/investors/onboarding-intent",
        json={
            "name": "Family Office Alpha",
            "email": "gp@familyoffice.com",
            "capital_band": "2m_10m",
            "track": "ib_self_directed",
            "exchange": "Bybit",
            "timeline": "1_3m",
            "message": "We manage $40M and want IB rev-share clarity.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "received"
    assert body["ticket_id"] >= 1


def test_investor_intent_validates_capital_band(client):
    r = client.post(
        "/investors/onboarding-intent",
        json={
            "name": "Bad Input",
            "email": "x@x.com",
            "capital_band": "thirty_dollars",  # invalid
        },
    )
    assert r.status_code == 422
