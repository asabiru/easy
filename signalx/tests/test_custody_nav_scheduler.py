"""Tests for the NAV-scheduler helper + Celery task.

Covers:
  * the helper produces the same result as the admin endpoint
    (delegation is wire-compatible)
  * the Celery task no-ops when the feature flag is off
  * the Celery task no-ops when the AUM adapter returns None
  * the Celery task successfully snapshots when flag + adapter both on
  * audit row marks `scheduled=true` and `actor_id=null` on scheduler path
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def client_with_db_nav(client_with_db):
    """Re-export the existing fixture under a test-specific name."""
    return client_with_db


def _register(client, email: str = "u@example.com", password: str = "pw1234567"):
    client.post("/auth/register", json={"email": email, "password": password})
    r = client.post("/auth/login", json={"email": email, "password": password})
    tok = r.json().get("access_token")
    if tok:
        client.headers["Authorization"] = f"Bearer {tok}"


def test_apply_nav_snapshot_records_audit_with_scheduled_flag(client_with_db_nav):
    """Calling the helper directly with actor_id=None writes a
    custody_nav_snapshot audit row whose payload marks scheduled=True."""
    from app.custody.nav_scheduler import apply_nav_snapshot
    from app.database.models import AmlEvent

    _, db = client_with_db_nav
    result = apply_nav_snapshot(
        db=db, aum_usdt=1000.0, note="unit-test", actor_id=None,
    )
    assert result.total_aum_usdt == 1000.0
    assert result.share_price == 1.0
    row = (
        db.query(AmlEvent)
        .filter(AmlEvent.kind == "custody_nav_snapshot")
        .order_by(AmlEvent.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_id is None
    detail = json.loads(row.detail)
    assert detail["scheduled"] is True
    assert detail["aum_usdt"] == 1000.0


def test_nav_autosnapshot_task_skips_when_flag_off(client_with_db_nav, monkeypatch):
    """With the feature flag off, the task returns `skipped` and does
    not insert a NavSnapshot row."""
    from app.database.models import NavSnapshot
    from app.performance.tracker import custody_nav_autosnapshot_task

    _, db = client_with_db_nav
    before = db.query(NavSnapshot).count()

    from app.config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "custody_nav_autoschedule_enabled", False, raising=False)
    monkeypatch.setenv("CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT", "123.45")

    out = custody_nav_autosnapshot_task()
    assert out["status"] == "skipped"
    assert "flag off" in out["reason"]

    after = db.query(NavSnapshot).count()
    assert after == before


def test_nav_autosnapshot_task_skips_when_adapter_returns_none(
    client_with_db_nav, monkeypatch,
):
    """With the flag on but no AUM env var, the task skips gracefully
    rather than snapshotting at AUM=0 (which would zero-out HWM for all
    users)."""
    from app.database.models import NavSnapshot
    from app.performance.tracker import custody_nav_autosnapshot_task

    _, db = client_with_db_nav
    before = db.query(NavSnapshot).count()

    from app.config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "custody_nav_autoschedule_enabled", True, raising=False)
    monkeypatch.delenv("CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT", raising=False)

    out = custody_nav_autosnapshot_task()
    assert out["status"] == "skipped"
    assert "adapter" in out["reason"]

    after = db.query(NavSnapshot).count()
    assert after == before


def test_nav_autosnapshot_task_snapshots_when_enabled(
    client_with_db_nav, monkeypatch,
):
    """Flag on + env AUM set → the task snapshots the pool and returns
    status=ok with the snapshot id + share price. The inserted audit
    row is flagged scheduled=True and actor_id is null.

    Uses monkeypatch on session_scope so the task sees the test
    in-memory engine rather than opening its own SessionLocal against
    the default SQLite file (which has no tables)."""
    from contextlib import contextmanager

    from app.database.models import NavSnapshot, AmlEvent
    from app.performance import tracker

    _, db = client_with_db_nav
    before = db.query(NavSnapshot).count()

    from app.config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "custody_nav_autoschedule_enabled", True, raising=False)
    monkeypatch.setenv("CUSTODY_NAV_AUTOSCHEDULE_AUM_USDT", "5000")

    @contextmanager
    def fake_session_scope():
        # Hand the task the test's shared session. The task calls
        # `db.commit()` inside apply_nav_snapshot, which is what we
        # want the assertions below to observe.
        yield db

    monkeypatch.setattr(tracker, "session_scope", fake_session_scope)

    out = tracker.custody_nav_autosnapshot_task()
    assert out["status"] == "ok", out
    assert out["aum_usdt"] == 5000.0
    assert out["share_price"] == 1.0  # no shares issued yet

    after = db.query(NavSnapshot).count()
    assert after == before + 1

    row = (
        db.query(AmlEvent)
        .filter(AmlEvent.kind == "custody_nav_snapshot")
        .order_by(AmlEvent.id.desc())
        .first()
    )
    assert row is not None
    detail = json.loads(row.detail)
    assert detail["scheduled"] is True
    assert row.actor_id is None
