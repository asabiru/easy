"""Aggregate performance metrics over signals."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import NewsEvent, Signal, SignalResult


def summary(db: Session) -> dict[str, Any]:
    signals: list[Signal] = db.query(Signal).all()
    by_action = Counter(s.action for s in signals)

    avg_impact = 0.0
    if signals:
        avg_impact = round(sum(s.signal_score for s in signals) / len(signals), 2)

    results = (
        db.query(SignalResult)
        .filter(SignalResult.result.isnot(None))
        .all()
    )
    wins = sum(1 for r in results if r.result == "win")
    losses = sum(1 for r in results if r.result == "loss")
    win_rate = round(wins / (wins + losses) * 100.0, 2) if (wins + losses) else None

    by_ticker: Counter[str] = Counter()
    by_event: Counter[str] = Counter()
    for s in signals:
        if s.action in ("LONG", "SHORT") and s.result and s.result.result == "win":
            by_ticker[s.ticker] += 1
            ev = (
                db.query(NewsEvent.event_type)
                .filter(NewsEvent.id == s.event_id)
                .scalar()
            )
            if ev:
                by_event[ev] += 1

    return {
        "total_signals": len(signals),
        "by_action": dict(by_action),
        "avg_signal_score": avg_impact,
        "win_rate_pct": win_rate,
        "best_tickers": by_ticker.most_common(5),
        "best_event_types": by_event.most_common(5),
    }
