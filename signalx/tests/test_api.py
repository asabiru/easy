"""End-to-end API smoke tests."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["autotrade_enabled"] is False  # MVP must NOT autotrade


def test_news_ingest_yields_signal(client):
    r = client.post(
        "/news/ingest",
        json={
            "source": "reuters",
            "source_url": "https://example.com/nvda",
            "raw_text": "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company"] == "Nvidia"
    assert body["ticker"] == "NVDA"
    assert body["symbol"] == "NVDAUSDT"
    assert body["event_type"] == "guidance_raised"
    assert body["direction"] == "bullish"
    assert body["action"] == "LONG"
    assert body["impact_score"] >= 80


def test_signals_list_after_ingest(client):
    client.post(
        "/news/ingest",
        json={
            "source": "reuters",
            "raw_text": "Tesla announces voluntary recall of Cybertruck over accelerator defect.",
        },
    )
    r = client.get("/signals")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["ticker"] == "TSLA"


def test_performance_summary_endpoint(client):
    client.post(
        "/news/ingest",
        json={
            "source": "reuters",
            "raw_text": "Nvidia raises Q2 revenue guidance above expectations.",
        },
    )
    r = client.get("/performance/summary")
    assert r.status_code == 200
    body = r.json()
    assert "total_signals" in body
    assert body["total_signals"] >= 1
