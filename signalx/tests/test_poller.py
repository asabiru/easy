"""Tests for the multi-source poller orchestrator."""
from __future__ import annotations

from unittest.mock import patch

from app.news.poller import _consume, poll_all_sources
from app.news.sources.types import IngestPayload, SourceFetchResult


def test_consume_skips_errors_and_counts_emits(db_session):
    """A single broken source must not stop subsequent sources from running,
    and successful pipeline emits must increment the counter."""
    err_result = SourceFetchResult(source_id="bad_feed", error="http 500")
    s = _consume(err_result, kind="rss", db=db_session)
    assert s.errors == ["http 500"]
    assert s.fetched == 0
    assert s.emitted_signals == 0


def test_consume_runs_payloads_through_pipeline(db_session):
    """Payloads from a successful fetch must flow through _run_pipeline.

    We mock _run_pipeline to count invocations; the real pipeline is
    exercised separately."""
    payload: IngestPayload = {
        "source": "reuters_business",
        "source_url": "https://example.com/a",
        "raw_text": "NVIDIA reports record earnings",
        "published_at": None,
    }
    result = SourceFetchResult(source_id="reuters_business", payloads=[payload])
    with patch("app.news.poller._run_pipeline", return_value={"signal_id": 1}) as mock:
        s = _consume(result, kind="rss", db=db_session)
    assert mock.call_count == 1
    assert s.fetched == 1
    assert s.emitted_signals == 1


def test_poll_all_sources_isolates_per_source_failures(db_session):
    """If one feed raises, the rest still poll."""
    with patch("app.news.poller.rss_feeds", return_value=[
        {"id": "broken_feed", "url": "https://invalid.example/feed"},
        {"id": "ok_feed", "url": "https://ok.example/feed"},
    ]), patch("app.news.poller.edgar_feeds", return_value=[]), \
         patch("app.news.poller.macro_energy_feeds", return_value=[]), \
         patch("app.news.poller.reddit_feeds", return_value=[]), \
         patch("app.news.poller.rss.fetch_rss", side_effect=[
             Exception("boom"),
             SourceFetchResult(source_id="ok_feed", payloads=[]),
         ]):
        summaries = poll_all_sources(db_session)
    assert len(summaries) == 2
    assert summaries[0].errors == ["boom"]
    assert summaries[1].errors is None


def test_admin_poll_now_requires_admin(client_with_db):
    """Regression for security skill: admin-only endpoint must reject
    anonymous + client roles."""
    client, db = client_with_db
    # anon
    r = client.post("/admin/news/poll-now")
    assert r.status_code == 401

    # client role
    from app.database.models import User
    from app.auth.security import hash_password
    db.add(User(email="c@example.com", password_hash=hash_password("p"),
                role="client", is_active=True))
    db.commit()
    client.post("/auth/login", json={"email": "c@example.com", "password": "p"})
    r = client.post("/admin/news/poll-now")
    assert r.status_code == 403


def test_admin_poll_now_writes_audit_log(client_with_db):
    """News-poll triggers an admin audit entry per security/eng-manager skill rules."""
    import json
    from app.database.models import AuditLog, User
    from app.auth.security import hash_password
    from unittest.mock import patch

    client, db = client_with_db
    db.add(User(email="adm@example.com", password_hash=hash_password("p"),
                role="admin", is_active=True))
    db.commit()
    r = client.post("/auth/login", json={"email": "adm@example.com", "password": "p"})
    assert r.status_code == 200

    with patch("app.news.poller.poll_all_sources", return_value=[]):
        r = client.post("/admin/news/poll-now")
    assert r.status_code == 200

    audits = db.query(AuditLog).filter(AuditLog.action == "news.poll_now").all()
    assert len(audits) == 1
    body = json.loads(audits[0].payload)
    assert body == {"sources": 0, "errors": 0}
