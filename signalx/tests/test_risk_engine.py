"""Risk engine tests."""
from app.signals.risk_engine import assess


def test_normal_market_no_override():
    a = assess(
        impact_score=80, confidence=80,
        spread_bps=5, volume_5m=20_000,
        price_change_5m=0.2, source_reliability=90,
    )
    assert a.action_override is None
    assert a.risk_level == "low"


def test_wide_spread_skips():
    a = assess(
        impact_score=80, confidence=80,
        spread_bps=80, volume_5m=20_000,
        price_change_5m=0.2, source_reliability=90,
    )
    assert a.action_override == "SKIP"
    assert a.risk_level == "high"


def test_thin_volume_skips():
    a = assess(
        impact_score=80, confidence=80,
        spread_bps=5, volume_5m=10,
        price_change_5m=0.2, source_reliability=90,
    )
    assert a.action_override == "SKIP"


def test_late_entry_watches():
    a = assess(
        impact_score=80, confidence=80,
        spread_bps=5, volume_5m=20_000,
        price_change_5m=3.0, source_reliability=90,
    )
    assert a.action_override == "WATCH"


def test_unreliable_source_watches():
    a = assess(
        impact_score=80, confidence=80,
        spread_bps=5, volume_5m=20_000,
        price_change_5m=0.2, source_reliability=30,
    )
    assert a.action_override == "WATCH"


def test_low_confidence_watches():
    a = assess(
        impact_score=80, confidence=40,
        spread_bps=5, volume_5m=20_000,
        price_change_5m=0.2, source_reliability=90,
    )
    assert a.action_override == "WATCH"


def test_low_impact_watches():
    a = assess(
        impact_score=30, confidence=80,
        spread_bps=5, volume_5m=20_000,
        price_change_5m=0.2, source_reliability=90,
    )
    assert a.action_override == "WATCH"
