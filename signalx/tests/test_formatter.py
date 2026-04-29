"""Telegram formatter tests."""
from app.signals.formatter import format_signal
from app.signals.signal_engine import SignalDecision


def _decision(action: str, **overrides) -> SignalDecision:
    base = dict(
        action=action,
        ticker="NVDA",
        symbol="NVDAUSDT",
        direction="bullish",
        impact_score=80,
        confidence=78,
        signal_score=82,
        risk_level="medium",
        reason="test",
        max_holding_minutes=15,
        entry_price=900.0,
        stop_loss=890.0,
        take_profit=915.0,
    )
    base.update(overrides)
    return SignalDecision(**base)


def test_long_signal_includes_entry_block():
    msg = format_signal(
        decision=_decision("LONG"),
        company_name="Nvidia",
        event_type="guidance_raised",
        source="reuters",
    )
    assert "Entry: 900" in msg
    assert "Stop: 890" in msg
    assert "Target: 915" in msg


def test_watch_signal_omits_entry_block_when_sl_tp_missing():
    """Devin Review BUG_0003 regression — WATCH signals had stop_loss=None /
    take_profit=None but entry_price=market.price, producing 'Stop: None' /
    'Target: None' lines in Telegram. We now omit the whole entry block in
    that case."""
    msg = format_signal(
        decision=_decision("WATCH", stop_loss=None, take_profit=None),
        company_name="Nvidia",
        event_type="other",
        source="reuters",
    )
    assert "Stop: None" not in msg
    assert "Target: None" not in msg
    assert "Entry:" not in msg


def test_skip_signal_no_entry_block():
    msg = format_signal(
        decision=_decision("SKIP", entry_price=None, stop_loss=None, take_profit=None),
        company_name="Nvidia",
        event_type="other",
        source="reuters",
    )
    assert "Entry:" not in msg
