"""KYC provider registry.

Looks at `settings.kyc_provider` and returns the matching adapter
instance. Falls back to MockProvider so tests + local dev never
need real credentials. Cached per-process — safe because adapters
are idempotent and configuration is read once at import time.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config.settings import get_settings
from app.kyc.providers.base import KycProvider
from app.kyc.providers.mock import MockProvider

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_provider() -> KycProvider:
    s = get_settings()
    name = (s.kyc_provider or "mock").lower()
    if name == "sumsub":
        try:
            from app.kyc.providers.sumsub import SumsubAdapter

            return SumsubAdapter()
        except Exception as exc:
            log.error(
                "Sumsub adapter unavailable (%s); falling back to MockProvider. "
                "DO NOT run prod with mock!",
                exc,
            )
            return MockProvider()
    if name == "mock":
        return MockProvider()
    log.warning("unknown KYC provider %r; falling back to mock", name)
    return MockProvider()


def reset_provider_cache() -> None:
    """Test helper: drop the lru_cache so subsequent calls re-read settings."""
    get_provider.cache_clear()
