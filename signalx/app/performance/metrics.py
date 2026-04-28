"""Aggregate performance metrics over signals."""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import NewsEvent, Signal, SignalResult


def _percentile(samples: list[float], pct: float) -> float | None:
    if not samples:
        return None
    samples = sorted(samples)
    k = max(0, min(len(samples) - 1, int(round(pct / 100.0 * (len(samples) - 1)))))
    return round(samples[k], 2)


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

    # End-to-end ingest → signal latency: derived from each signal's
    # created_at minus its NewsEvent.received_at.
    latencies_ms: list[float] = []
    for s in signals:
        ev = (
            db.query(NewsEvent.received_at)
            .filter(NewsEvent.id == s.event_id)
            .scalar()
        )
        if ev and s.created_at:
            delta = (s.created_at - ev).total_seconds() * 1000.0
            if delta >= 0:
                latencies_ms.append(delta)

    latency = {
        "samples": len(latencies_ms),
        "p50_ms": _percentile(latencies_ms, 50),
        "p95_ms": _percentile(latencies_ms, 95),
        "max_ms": round(max(latencies_ms), 2) if latencies_ms else None,
        "avg_ms": round(statistics.fmean(latencies_ms), 2) if latencies_ms else None,
    }

    # fake_risk distribution across processed events (simple counters; useful
    # to monitor whether the anti-fake gate is actually firing in production).
    fake_buckets = {"clean_lt30": 0, "watch_30_49": 0, "skip_ge50": 0}
    fake_rows = db.query(NewsEvent.fake_risk).filter(NewsEvent.fake_risk.isnot(None)).all()
    for (fr,) in fake_rows:
        if fr is None:
            continue
        if fr >= 50:
            fake_buckets["skip_ge50"] += 1
        elif fr >= 30:
            fake_buckets["watch_30_49"] += 1
        else:
            fake_buckets["clean_lt30"] += 1

    return {
        "total_signals": len(signals),
        "by_action": dict(by_action),
        "avg_signal_score": avg_impact,
        "win_rate_pct": win_rate,
        "best_tickers": by_ticker.most_common(5),
        "best_event_types": by_event.most_common(5),
        "latency": latency,
        "fake_risk_distribution": fake_buckets,
    }
