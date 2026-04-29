"""Spread helpers."""
from __future__ import annotations


def is_spread_acceptable(spread_bps: float | None, max_bps: float = 30.0) -> bool:
    """30 bps default; tighten per ticker as needed."""
    if spread_bps is None:
        return False
    return spread_bps <= max_bps
