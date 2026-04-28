"""SignalX FastAPI entrypoint."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_admin,
    routes_auth,
    routes_autotrade,
    routes_client,
    routes_health,
    routes_investors,
    routes_kyc,
    routes_manager,
    routes_payments,
    routes_referral,
    routes_news,
    routes_signals,
    routes_support,
)
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
    app.include_router(routes_autotrade.router, tags=["autotrade"])
    app.include_router(routes_investors.router, tags=["investors"])
    app.include_router(routes_auth.router, tags=["auth"])
    app.include_router(routes_client.router, tags=["client"])
    app.include_router(routes_manager.router, tags=["manager"])
    app.include_router(routes_admin.router, tags=["admin"])
    app.include_router(routes_kyc.router, tags=["kyc"])
    app.include_router(routes_payments.router, tags=["payments"])
    app.include_router(routes_referral.router, tags=["referral"])

    # CORS — accept configured origins or default to permissive for MVP
    origins_csv = os.environ.get("CORS_ORIGINS", "*")
    allow_origins = [o.strip() for o in origins_csv.split(",") if o.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static site — serve /, /app.html, /admin.html, /manager.html etc. from
    # the same backend. In production the static frontend can also live on a
    # separate CDN; this fallback keeps single-port dev simple.
    site_dir = Path(__file__).resolve().parents[1] / "site"
    if site_dir.exists():
        @app.get("/", include_in_schema=False)
        def _index() -> FileResponse:
            return FileResponse(site_dir / "index.html")

        app.mount("/assets", StaticFiles(directory=site_dir / "assets"), name="assets")

        # Serve /legal/* (risk disclosures, privacy, etc.) as a static dir.
        # Compliance owns these — never return 200 if the dir is missing.
        legal_dir = site_dir / "legal"
        if legal_dir.exists():
            app.mount(
                "/legal",
                StaticFiles(directory=legal_dir, html=True),
                name="legal",
            )

        for page in ("app", "admin", "manager", "login", "signup", "miniapp"):
            html_path = site_dir / f"{page}.html"
            if html_path.exists():
                def _factory(path: Path):
                    def _serve() -> FileResponse:
                        return FileResponse(path)
                    return _serve
                app.add_api_route(
                    f"/{page}.html",
                    _factory(html_path),
                    methods=["GET"],
                    include_in_schema=False,
                )

    return app


app = create_app()
