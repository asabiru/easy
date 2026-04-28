"""Liquidity helpers."""
from __future__ import annotations


def is_liquid(volume_5m: float | None, min_volume: float = 5_000.0) -> bool:
    if volume_5m is None:
        return False
    return volume_5m >= min_volume


def has_late_entry_risk(price_change_5m: float | None, threshold_pct: float = 1.5) -> bool:
    """If symbol has already moved >1.5% in 5m, late entry risk is high."""
    if price_change_5m is None:
        return False
    return abs(price_change_5m) >= threshold_pct
