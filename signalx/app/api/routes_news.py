"""News ingest pipeline endpoint.

POST /news/ingest runs the full pipeline:
  normalize → dedup → company match → classify → sentiment → impact →
  market check → risk → signal → telegram → persist.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analysis.classifier import classify
from app.analysis.impact_score import compute_impact
from app.analysis.sentiment import refine
from app.companies.mapper import find_company
from app.database.models import MarketSnapshot, NewsEvent, Signal
from app.database.session import get_db
from app.market.exchange_client import get_exchange_client
from app.news.deduplicator import is_duplicate, text_hash
from app.news.normalizer import normalize
from app.news.source_reliability import reliability_score
from app.notifications.telegram import send as telegram_send
from app.signals.formatter import format_signal
from app.signals.signal_engine import decide

log = logging.getLogger(__name__)
router = APIRouter()


class NewsIngest(BaseModel):
    source: str = Field(..., examples=["reuters"])
    source_url: str | None = None
    raw_text: str = Field(..., min_length=3)
    published_at: datetime | None = None


@router.post("/news/ingest")
def ingest_news(payload: NewsIngest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        normalized = normalize(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    h = text_hash(normalized.normalized_text)
    existing = db.query(NewsEvent).filter(NewsEvent.text_hash == h).first()
    duplicate = bool(existing) or is_duplicate(normalized.normalized_text)

    match = find_company(normalized.normalized_text)
    if match is None:
        event = _persist_news(
            db, normalized, h,
            company=None, ticker=None, event_type=None,
            direction=None, confidence=None, impact=None,
            duplicate=duplicate,
        )
        return {
            "event_id": event.id,
            "is_duplicate": duplicate,
            "company": None,
            "action": "SKIP",
            "reason": "no company matched",
        }

    classification = refine(classify(normalized.normalized_text), normalized.normalized_text)
    impact = compute_impact(classification)

    market = get_exchange_client().fetch_market_data(match.company.exchange_symbol)
    src_rel = reliability_score(normalized.source)

    decision = decide(
        classification=classification,
        impact_score=impact,
        company_ticker=match.company.ticker,
        symbol=match.company.exchange_symbol,
        market=market,
        source_reliability=src_rel,
    )

    event = _persist_news(
        db,
        normalized,
        h,
        company=match.company.company,
        ticker=match.company.ticker,
        event_type=classification.event_type,
        direction=classification.direction,
        confidence=classification.confidence,
        impact=impact,
        duplicate=duplicate,
    )

    snapshot = MarketSnapshot(
        event_id=event.id,
        exchange=market.exchange,
        symbol=market.symbol,
        price=market.price,
        bid=market.bid,
        ask=market.ask,
        spread=market.spread,
        volume_1m=market.volume_1m,
        volume_5m=market.volume_5m,
        price_change_1m=market.price_change_1m,
        price_change_5m=market.price_change_5m,
        funding_rate=market.funding_rate,
        open_interest=market.open_interest,
    )
    db.add(snapshot)

    signal = Signal(
        event_id=event.id,
        ticker=decision.ticker,
        symbol=decision.symbol,
        direction=decision.direction,
        signal_score=decision.signal_score,
        action=decision.action,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        max_holding_minutes=decision.max_holding_minutes,
        risk_level=decision.risk_level,
        reason=decision.reason,
        status="new",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    if decision.action in ("LONG", "SHORT", "WATCH"):
        msg = format_signal(
            decision=decision,
            company_name=match.company.company,
            event_type=classification.event_type,
            source=normalized.source,
            market_state="liquid" if (market.volume_5m or 0) >= 5_000 else "thin",
        )
        try:
            telegram_send(msg)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("telegram_send raised: %s", exc)

    return {
        "event_id": event.id,
        "signal_id": signal.id,
        "is_duplicate": duplicate,
        "company": match.company.company,
        "ticker": decision.ticker,
        "symbol": decision.symbol,
        "event_type": classification.event_type,
        "direction": decision.direction,
        "impact_score": decision.impact_score,
        "confidence": decision.confidence,
        "signal_score": decision.signal_score,
        "action": decision.action,
        "risk_level": decision.risk_level,
        "reason": decision.reason,
    }


def _persist_news(
    db: Session,
    normalized,
    h: str,
    *,
    company: str | None,
    ticker: str | None,
    event_type: str | None,
    direction: str | None,
    confidence: float | None,
    impact: int | None,
    duplicate: bool,
) -> NewsEvent:
    event = NewsEvent(
        received_at=normalized.received_at,
        published_at=normalized.published_at,
        source=normalized.source,
        source_url=normalized.source_url,
        raw_text=normalized.raw_text,
        normalized_text=normalized.normalized_text,
        text_hash=h,
        company=company,
        ticker=ticker,
        event_type=event_type,
        direction=direction,
        confidence=confidence,
        impact_score=impact,
        urgency=None,
        is_duplicate=duplicate,
        fake_risk=0.0,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
