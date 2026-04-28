"""Pytest fixtures: in-memory SQLite DB, mock exchange, stubbed Telegram."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make `signalx/` importable as a path root so `app...` imports resolve.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force test-friendly env BEFORE importing app modules.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("EXCHANGE_USE_MOCK", "true")
os.environ.setdefault("TELEGRAM_ENABLED", "false")
os.environ.setdefault("APP_ENV", "dev")


@pytest.fixture(scope="session")
def settings():
    from app.config.settings import get_settings

    return get_settings()


def _make_inmemory_engine():
    """SQLite in-memory engine that survives multi-thread access (TestClient)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    return create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture()
def db_session():
    """Fresh SQLAlchemy session backed by SQLite in-memory."""
    from sqlalchemy.orm import sessionmaker

    from app.database.session import Base
    import app.database.models  # noqa: F401  register models

    engine = _make_inmemory_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def client(monkeypatch):
    """FastAPI TestClient with overridden DB dependency → in-memory SQLite."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.database.session import Base, get_db
    import app.database.models  # noqa: F401

    engine = _make_inmemory_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()
