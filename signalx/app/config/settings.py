"""Centralised configuration via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    enable_autotrade: bool = False  # MVP v0.1 must NEVER auto-trade.

    # Database
    database_url: str = "postgresql+psycopg2://signalx:signalx@db:5432/signalx"
    postgres_user: str = "signalx"
    postgres_password: str = "signalx"
    postgres_db: str = "signalx"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # Exchange
    exchange_id: str = "binance"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    exchange_use_mock: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False

    # X (Twitter) integration
    x_api_bearer_token: str = ""
    x_stream_enabled: bool = False
    x_webhook_secret: str = ""  # Shared secret for /news/ingest/x

    # Cross-source confirmation window
    cross_source_use_redis: bool = False
    cross_source_redis_url: str = ""
    cross_source_window_sec: int = 90

    # Auto-trade subscription module. `enable_autotrade` (above) is the
    # *global* hard kill — even with a subscription opt-in, no real orders
    # are placed unless this is true. Per-subscription opt-in is also
    # required (see AutoTradeSubscription.live_trading_enabled).
    autotrade_encryption_key: str = ""  # 32-byte url-safe base64 (Fernet)
    autotrade_default_paper_days: int = 7
    autotrade_default_max_position_pct: float = 0.10
    autotrade_default_daily_loss_limit_pct: float = 0.05

    # Data files
    data_dir: Path = Field(default_factory=lambda: DATA_DIR)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
