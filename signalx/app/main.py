"""SignalX FastAPI entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api import routes_health, routes_news, routes_signals, routes_support
from app.config.settings import get_settings
from app.database.session import init_db


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title="SignalX Stock Futures News Trading System",
        description=(
            "MVP v0.1 — event-driven trading signals for tokenized stock perpetual "
            "futures. NOTE: this MVP does NOT execute live trades."
        ),
        version="0.1.0",
    )

    @app.on_event("startup")
    def _startup() -> None:
        try:
            init_db()
        except Exception as exc:  # pragma: no cover
            logging.getLogger(__name__).warning(
                "init_db failed (probably DB not reachable yet): %s", exc
            )

    app.include_router(routes_health.router, tags=["health"])
    app.include_router(routes_news.router, tags=["news"])
    app.include_router(routes_signals.router, tags=["signals"])
    app.include_router(routes_support.router, tags=["support"])
    return app


app = create_app()
