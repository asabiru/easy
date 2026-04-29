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
    news_ingest_secret: str = ""  # Shared secret for /news/ingest, /discord, /rss

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

    # Auth (JWT-cookie + role-based access control)
    jwt_secret: str = ""
    jwt_session_ttl_sec: int = 60 * 60 * 12
    bootstrap_admin_email: str = ""  # optional: auto-promotes this email to admin on registration
    autotrade_default_max_position_pct: float = 0.10
    autotrade_default_daily_loss_limit_pct: float = 0.05

    # KYC / AML
    # `kyc_provider` selects the adapter in app.kyc.registry.
    # `mock` (default) is a no-op fallback for tests + local dev — DO NOT
    # use in any environment with real users.
    # `sumsub` requires SUMSUB_APP_TOKEN + SUMSUB_SECRET_KEY; falls back
    # to mock if either is missing (logs a loud error).
    kyc_provider: Literal["mock", "sumsub"] = "mock"
    kyc_required: bool = False  # When True, /autotrade/* + /payments/* refuse non-approved users.
    sumsub_app_token: str = ""
    sumsub_secret_key: str = ""
    sumsub_level_name: str = "basic-kyc-level"

    # TON / Telegram Wallet payments
    ton_wallet_pay_api_key: str = ""  # https://pay.wallet.tg merchant key
    ton_wallet_pay_webhook_secret: str = ""
    ton_treasury_address: str = ""  # Mainnet UQ... address that receives subscription/vault deposits
    ton_network: Literal["mainnet", "testnet"] = "testnet"

    # ────────────────────────── Custody / managed pool ────────────────────── #
    # When True, /wallet/deposit-address surfaces a real address and
    # accepts production-mode webhooks. Default OFF so a misconfigured
    # deploy can't accidentally take real client money.
    custody_live_deposits_enabled: bool = False
    # Free-text licence attestation surfaced in /legal/disclosures and
    # checked at every deposit. Empty = MVP / pre-licence; the deposit
    # endpoint then refuses with 503 unless the operator-override flag
    # is set.
    custody_license_jurisdiction: str = ""
    custody_license_number: str = ""
    custody_self_attest_override: bool = False  # operator opt-in to accept liability without licence
    # Performance / management fee tunables — match the spec in
    # `docs/vault-spec.md`.
    custody_perf_fee_pct: float = 0.20
    custody_mgmt_fee_pct_annual: float = 0.02
    # Withdrawals are queued for manual operator approval before send.
    # Cooldown prevents flush-attacks (deposit → instantly withdraw to
    # round-trip USDT through us). 24h matches what major CEXes do.
    custody_withdraw_cooldown_hours: int = 24
    custody_min_withdraw_usdt: float = 10.0
    # Hourly Celery beat task that snapshots NAV + accrues perf fees.
    # Default OFF — the operator still calls the endpoint manually in
    # MVP. Flip to True only after the AUM adapter returns a real number.
    # See app/custody/nav_scheduler.py for the stub env-var adapter.
    custody_nav_autoschedule_enabled: bool = False

    # Webhook HMAC secrets — one per chain. Empty = chain-disabled
    # (POST /payments/<chain>/webhook returns 503). All secrets MUST be
    # rotated post-incident. The webhook handler verifies the chain-
    # specific signature scheme — see app/custody/webhooks.py.
    custody_trongrid_webhook_secret: str = ""   # TronGrid HMAC-SHA256
    custody_alchemy_webhook_secret: str = ""    # Alchemy "X-Alchemy-Signature" HMAC-SHA256
    custody_helius_webhook_secret: str = ""     # Helius shared "Authorization" bearer token
    custody_bscscan_webhook_secret: str = ""    # custom listener HMAC-SHA256
    # TON Wallet Pay already has its own ton_wallet_pay_webhook_secret;
    # we reuse it here for managed-pool deposits.

    # Data files
    data_dir: Path = Field(default_factory=lambda: DATA_DIR)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
