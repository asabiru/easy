"""News ingest pipeline endpoints.

Pipeline:
  normalize → dedup → company match → classify → sentiment → impact →
  market check → cross-source → fake_risk → risk → signal → telegram → persist.

Endpoints:
  POST /news/ingest      — generic ingest (RSS / manual / press release).
  POST /news/ingest/x    — X (Twitter) webhook ingest with author metadata for
                            fake_risk scoring.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analysis.classifier import EventClassification, classify
from app.analysis.fake_risk import AuthorMeta, compute_fake_risk
from app.analysis.impact_score import compute_impact
from app.analysis.sentiment import refine
from app.companies.mapper import find_company
from app.config.settings import get_settings
from app.database.models import MarketSnapshot, NewsEvent, Signal
from app.database.session import get_db
from app.market.exchange_client import get_exchange_client
from app.news.cross_source import record_observation
from app.news.deduplicator import is_duplicate, text_hash
from app.news.normalizer import normalize
from app.news.source_reliability import reliability_score, x_handle_meta
from app.notifications.telegram import send as telegram_send
from app.signals.formatter import format_signal
from app.signals.signal_engine import decide

log = logging.getLogger(__name__)
router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic payloads                                                            #
# --------------------------------------------------------------------------- #
class NewsIngest(BaseModel):
    source: str = Field(..., examples=["reuters"])
    source_url: str | None = None
    raw_text: str = Field(..., min_length=3)
    published_at: datetime | None = None


class XIngest(BaseModel):
    """Webhook payload for X / Twitter posts. Mirrors v2 tweet object fields."""

    handle: str = Field(..., examples=["DeItaone"])
    raw_text: str = Field(..., min_length=3)
    tweet_url: str | None = None
    published_at: datetime | None = None

    # Author metadata used by fake_risk
    verified: bool | None = None
    followers: int | None = None
    account_age_days: int | None = None
    has_authoritative_link: bool = False


class DiscordIngest(BaseModel):
    """Webhook payload for posts forwarded from a Discord channel — typically
    a prop-desk or analyst alerts channel proxied by a small relay bot."""

    channel: str = Field(..., examples=["alpha-desk-alerts"])
    raw_text: str = Field(..., min_length=3)
    message_url: str | None = None
    published_at: datetime | None = None
    author: str | None = None


class RSSIngest(BaseModel):
    """Webhook payload for RSS / Atom feed entries forwarded by an external
    poller. The shape is a thin transport for `IngestPayload`."""

    feed_id: str = Field(..., examples=["reuters_business"])
    raw_text: str = Field(..., min_length=3)
    entry_url: str | None = None
    published_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Shared pipeline                                                              #
# --------------------------------------------------------------------------- #
def _run_pipeline(
    *,
    db: Session,
    raw_payload: dict[str, Any],
    author: AuthorMeta | None,
    source_id_for_xsrc: str,
) -> dict[str, Any]:
    """Single source-of-truth ingest pipeline. Both /news/ingest and
    /news/ingest/x funnel through this function."""

    try:
        normalized = normalize(raw_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    h = text_hash(normalized.normalized_text)
    existing = db.query(NewsEvent).filter(NewsEvent.text_hash == h).first()
    duplicate = bool(existing) or is_duplicate(normalized.normalized_text)

    match = find_company(normalized.normalized_text)
    # Short-circuit BOTH cases: no company match, AND duplicate-of-existing.
    # Duplicates must never re-fire signals or Telegram alerts.
    if match is None or duplicate:
        event = _persist_news(
            db, normalized, h,
            company=match.company.company if match else None,
            ticker=match.company.ticker if match else None,
            event_type=None,
            direction=None, confidence=None, impact=None,
            duplicate=duplicate, fake_risk=0,
        )
        db.commit()
        db.refresh(event)
        return {
            "event_id": event.id,
            "is_duplicate": duplicate,
            "company": match.company.company if match else None,
            "action": "SKIP",
            "reason": "duplicate news" if duplicate else "no company matched",
        }

    classification: EventClassification = refine(
        classify(normalized.normalized_text), normalized.normalized_text
    )
    impact = compute_impact(classification)

    market = get_exchange_client().fetch_market_data(match.company.exchange_symbol)
    src_rel = reliability_score(normalized.source)

    # Cross-source confirmation: how many distinct sources have published this
    # ticker+event in the rolling window? Boosts confidence + lowers fake_risk.
    confirmation_count = record_observation(
        ticker=match.company.ticker,
        event_type=classification.event_type or "other",
        source_id=source_id_for_xsrc,
    )

    # Anti-fake scoring
    fake_risk = compute_fake_risk(
        text=normalized.normalized_text,
        author=author,
        confirmation_count=confirmation_count,
        pre_tweet_price_change_pct=market.price_change_1m,
    )

    decision = decide(
        classification=classification,
        impact_score=impact,
        company_ticker=match.company.ticker,
        symbol=match.company.exchange_symbol,
        market=market,
        source_reliability=src_rel,
        fake_risk=fake_risk,
        confirmation_count=confirmation_count,
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
        fake_risk=fake_risk,
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

    # End-to-end latency: receive → signal emitted.
    latency_ms = None
    if normalized.received_at is not None:
        # The "now" measurement here is monotonic-ish enough for an MVP metric.
        delta = (datetime.now(timezone.utc).replace(tzinfo=None) - normalized.received_at)
        latency_ms = int(delta.total_seconds() * 1000)

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
        "fake_risk": fake_risk,
        "confirmation_count": confirmation_count,
        "latency_ms": latency_ms,
    }


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #
@router.post("/news/ingest")
def ingest_news(payload: NewsIngest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Generic news webhook (RSS, manual, press release).

    Author metadata is unknown → AuthorMeta() neutral, fake_risk gets a
    baseline score driven mainly by source reliability + linguistic markers.
    """
    return _run_pipeline(
        db=db,
        raw_payload=payload.model_dump(),
        author=AuthorMeta(is_known_press=reliability_score(payload.source) >= 80),
        source_id_for_xsrc=payload.source.lower(),
    )


@router.post("/news/ingest/x")
def ingest_news_x(
    payload: XIngest,
    db: Session = Depends(get_db),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, Any]:
    """X (Twitter) webhook. Optionally protected by `X-Signature` shared
    secret if `X_WEBHOOK_SECRET` is configured."""
    s = get_settings()
    if s.x_webhook_secret and x_signature != s.x_webhook_secret:
        raise HTTPException(status_code=401, detail="invalid X-Signature")

    handle = payload.handle.lstrip("@")
    meta = x_handle_meta(handle) or {}
    tier = meta.get("tier", "")
    author = AuthorMeta(
        handle=handle,
        verified=payload.verified,
        followers=payload.followers,
        account_age_days=payload.account_age_days,
        has_authoritative_link=payload.has_authoritative_link,
        is_known_official=tier == "official",
        is_known_press=tier in ("press", "wire"),
    )

    raw_payload = {
        "source": f"x:{handle}",
        "source_url": payload.tweet_url,
        "raw_text": payload.raw_text,
        "published_at": payload.published_at,
    }
    return _run_pipeline(
        db=db,
        raw_payload=raw_payload,
        author=author,
        source_id_for_xsrc=f"x:{handle.lower()}",
    )


@router.post("/news/ingest/discord")
def ingest_news_discord(
    payload: DiscordIngest,
    db: Session = Depends(get_db),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, Any]:
    """Discord-relay webhook. Same shape as X but no follower/age metadata —
    fake_risk falls back to the linguistic + cross-source layer."""
    s = get_settings()
    secret = s.x_webhook_secret  # we re-use the same shared-secret slot
    if secret and x_signature != secret:
        raise HTTPException(status_code=401, detail="invalid X-Signature")

    raw_payload = {
        "source": f"discord:{payload.channel.lower()}",
        "source_url": payload.message_url,
        "raw_text": payload.raw_text,
        "published_at": payload.published_at,
    }
    return _run_pipeline(
        db=db,
        raw_payload=raw_payload,
        author=AuthorMeta(handle=payload.author),
        source_id_for_xsrc=f"discord:{payload.channel.lower()}",
    )


@router.post("/news/ingest/rss")
def ingest_news_rss(
    payload: RSSIngest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Generic RSS / Atom item passthrough — the external poller has already
    de-duplicated by entry-id; we still apply our normalized-text dedup."""
    raw_payload = {
        "source": payload.feed_id.lower(),
        "source_url": payload.entry_url,
        "raw_text": payload.raw_text,
        "published_at": payload.published_at,
    }
    return _run_pipeline(
        db=db,
        raw_payload=raw_payload,
        author=AuthorMeta(is_known_press=reliability_score(payload.feed_id) >= 80),
        source_id_for_xsrc=payload.feed_id.lower(),
    )


# --------------------------------------------------------------------------- #
# DB helpers                                                                   #
# --------------------------------------------------------------------------- #
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
    fake_risk: int = 0,
) -> NewsEvent:
    """Add a NewsEvent and flush to obtain its id, without committing.

    The caller commits once after MarketSnapshot + Signal are attached so the
    whole ingest is atomic — preventing orphan NewsEvent rows if the snapshot
    or signal write fails."""
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
        fake_risk=float(fake_risk),
    )
    db.add(event)
    db.flush()
    db.refresh(event)
    return event
