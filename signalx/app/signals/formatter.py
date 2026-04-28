"""Telegram message formatter for SignalDecision."""
from __future__ import annotations

from app.signals.signal_engine import SignalDecision


_RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}
_ACTION_EMOJI = {"LONG": "🟩 LONG", "SHORT": "🟥 SHORT", "WATCH": "👀 WATCH", "SKIP": "⏭ SKIP"}


def format_signal(
    *,
    decision: SignalDecision,
    company_name: str,
    event_type: str,
    source: str,
    market_state: str = "liquid",
) -> str:
    risk = _RISK_EMOJI.get(decision.risk_level, "⚪")
    action = _ACTION_EMOJI.get(decision.action, decision.action)
    lines = [
        "🚨 NEWS SIGNAL",
        "",
        f"Company: {company_name}",
        f"Ticker: {decision.ticker}",
        f"Instrument: {decision.symbol} Perp",
        "",
        f"Action: {action}",
        f"Impact: {decision.impact_score}/100",
        f"Confidence: {decision.confidence}%",
        f"Signal score: {decision.signal_score}/100",
        f"Risk: {risk} {decision.risk_level.title()}",
        f"Max holding: {decision.max_holding_minutes} min",
        "",
        f"Event: {event_type}",
        f"Source: {source}",
        f"Market: {market_state}",
        "",
        "Reason:",
        decision.reason,
    ]
    if decision.entry_price is not None:
        lines += [
            "",
            f"Entry: {decision.entry_price}",
            f"Stop: {decision.stop_loss}",
            f"Target: {decision.take_profit}",
        ]
    return "\n".join(lines)
