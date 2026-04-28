"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter

from app.config.settings import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "app_env": s.app_env,
        "exchange_use_mock": s.exchange_use_mock,
        "telegram_enabled": s.telegram_enabled,
        "autotrade_enabled": s.enable_autotrade,
    }
