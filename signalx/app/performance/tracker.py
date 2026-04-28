"""Performance tracker — sample price after 1m/3m/5m/15m and compute results.

MVP v0.1 wires Celery beat for the periodic sweep; the Celery worker is optional
in dev (you can also call `update_pending_results()` from a cron / manual call).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from celery import Celery
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.models import Signal, SignalResult
from app.database.session import session_scope
from app.market.exchange_client import fetch_price_at

log = logging.getLogger(__name__)

_settings = get_settings()
celery_app = Celery(
    "signalx",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)
celery_app.conf.beat_schedule = {
    "update-pending-results-every-30s": {
        "task": "app.performance.tracker.update_pending_results_task",
        "schedule": 30.0,
    }
}
celery_app.conf.timezone = "UTC"


_SNAPSHOT_OFFSETS = [
    ("price_after_1m", timedelta(minutes=1)),
    ("price_after_3m", timedelta(minutes=3)),
    ("price_after_5m", timedelta(minutes=5)),
    ("price_after_15m", timedelta(minutes=15)),
]


def _ensure_result(db: Session, signal: Signal) -> SignalResult:
    if signal.result is None:
        signal.result = SignalResult(signal_id=signal.id)
        db.add(signal.result)
        db.flush()
    return signal.result


def _classify_result(direction: str, entry: float | None, last: float | None) -> str:
    if entry is None or last is None:
        return "unknown"
    delta = (last - entry) / entry * 100.0
    if direction == "bullish":
        return "win" if delta > 0.2 else ("loss" if delta < -0.2 else "neutral")
    if direction == "bearish":
        return "win" if delta < -0.2 else ("loss" if delta > 0.2 else "neutral")
    return "neutral"


def update_signal_result(db: Session, signal: Signal) -> SignalResult:
    res = _ensure_result(db, signal)
    now = datetime.utcnow()
    if signal.created_at is None:
        return res

    fav = res.max_favorable_move or 0.0
    adv = res.max_adverse_move or 0.0
    last_price: float | None = None

    for field, offset in _SNAPSHOT_OFFSETS:
        if getattr(res, field) is not None:
            continue
        if now < signal.created_at + offset:
            continue
        price = fetch_price_at(signal.symbol)
        if price is None:
            continue
        setattr(res, field, price)
        last_price = price
        if signal.entry_price:
            move_pct = (price - signal.entry_price) / signal.entry_price * 100.0
            if signal.direction == "bearish":
                move_pct = -move_pct
            fav = max(fav, move_pct)
            adv = min(adv, move_pct)

    res.max_favorable_move = fav
    res.max_adverse_move = adv
    if last_price is not None:
        res.result = _classify_result(signal.direction, signal.entry_price, last_price)
    res.updated_at = datetime.utcnow()
    return res


def update_pending_results(signal_ids: Iterable[int] | None = None) -> int:
    """Iterate signals that still need samples; return count updated."""
    updated = 0
    cutoff = datetime.utcnow() - timedelta(minutes=20)
    with session_scope() as db:
        q = db.query(Signal).filter(Signal.action.in_(["LONG", "SHORT"]))
        if signal_ids is not None:
            q = q.filter(Signal.id.in_(list(signal_ids)))
        else:
            q = q.filter(Signal.created_at >= cutoff - timedelta(hours=1))
        for sig in q.all():
            try:
                update_signal_result(db, sig)
                updated += 1
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("update_signal_result failed id=%s err=%s", sig.id, exc)
    return updated


@celery_app.task(name="app.performance.tracker.update_pending_results_task")
def update_pending_results_task() -> int:
    return update_pending_results()
