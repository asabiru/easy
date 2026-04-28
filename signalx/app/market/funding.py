"""Funding-rate helpers (perpetual futures)."""
from __future__ import annotations


def funding_signal(funding_rate: float | None) -> str:
    """Return a coarse description of funding bias."""
    if funding_rate is None:
        return "unknown"
    if funding_rate > 0.0005:
        return "longs_paying"
    if funding_rate < -0.0005:
        return "shorts_paying"
    return "balanced"
