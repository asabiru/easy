"""Multi-source news poller orchestrator.

Polls every configured RSS / EDGAR / macro / Reddit / Mastodon / BlueSky /
StockTwits source and forwards each new payload through the same in-process
pipeline that the `POST /news/ingest*` endpoints use, so cross-source
confirmation, fake-risk scoring, dedup, and signal emission all stay in one
place.

Designed to be safe to call from:

  * an admin endpoint (`POST /admin/news/poll-now`)
  * a CLI: `python -m app.news.poll_now`
  * a background task / Celery beat (when added)

Network failures on individual sources never crash the loop — they're logged
and the source's `error` is bubbled up in the per-source result so the admin
UI can display a health summary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.api.routes_news import _run_pipeline
from app.analysis.fake_risk import AuthorMeta
from app.news.source_reliability import reliability_score
from app.news.sources import mastodon as mastodon_src
from app.news.sources import reddit, rss, sec_edgar
from app.news.sources import stocktwits as stocktwits_src
from app.news.sources.registry import (
    edgar_feeds,
    macro_energy_feeds,
    mastodon_feeds,
    reddit_feeds,
    rss_feeds,
    stocktwits_config,
)
from app.news.sources.types import IngestPayload, SourceFetchResult

log = logging.getLogger(__name__)


@dataclass
class PollSummary:
    source_id: str
    kind: str
    fetched: int = 0
    emitted_signals: int = 0
    errors: list[str] | None = None


def poll_all_sources(db: Session) -> list[PollSummary]:
    """Poll every configured source once. Returns a per-source summary so the
    caller (admin UI, CLI, cron) can show health + emitted-signal counts.

    The function intentionally swallows per-source errors — a single broken
    feed must never block the rest. All errors are logged + included in the
    return value.
    """
    summaries: list[PollSummary] = []

    summaries.extend(_poll_kind("rss", rss_feeds(), rss.fetch_rss, db))
    summaries.extend(_poll_kind("sec_edgar", edgar_feeds(), sec_edgar.fetch_edgar, db))
    summaries.extend(_poll_kind("macro_energy", macro_energy_feeds(), rss.fetch_rss, db))
    summaries.extend(_poll_kind("reddit", reddit_feeds(), reddit.fetch_subreddit, db))
    summaries.extend(_poll_mastodon(mastodon_feeds(), db))
    summaries.extend(_poll_stocktwits(db))

    return summaries


def _poll_mastodon(feeds: list[dict], db: Session) -> list[PollSummary]:
    """Mastodon collector takes (instance, tag) instead of (source_id, url)."""
    out: list[PollSummary] = []
    for feed in feeds:
        sid = feed.get("id") or f"mastodon:{feed.get('tag', '?')}"
        instance = feed.get("instance") or "https://mastodon.social"
        tag = feed.get("tag", "")
        if not tag:
            out.append(PollSummary(source_id=sid, kind="mastodon", errors=["missing tag"]))
            continue
        try:
            result = mastodon_src.fetch_tag(sid, instance, tag)
        except Exception as exc:
            log.exception("poller mastodon failed for %s: %s", sid, exc)
            out.append(PollSummary(source_id=sid, kind="mastodon", errors=[str(exc)]))
            continue
        out.append(_consume(result, "mastodon", db))
    return out


def _poll_stocktwits(db: Session) -> list[PollSummary]:
    """StockTwits collector polls one symbol-stream per equity in the universe.

    We read the symbol allow-list from companies.json (loaded lazily here
    to avoid a circular import) and skip crypto tickers (sector starts with
    "crypto") because StockTwits cashtag streams there are dominated by
    pump-and-dump posts that the fake_risk scorer already filters at
    extreme cost. Disabled if `stocktwits.enabled` is False.
    """
    cfg = stocktwits_config()
    if not cfg.get("enabled", True):
        return []

    # Lazy import to avoid pulling the universe loader into module import time.
    from app.companies.universe import load_universe

    out: list[PollSummary] = []
    for c in load_universe():
        ticker = c.ticker
        sector = (c.sector or "").lower()
        if not ticker or sector.startswith("crypto"):
            continue
        try:
            result = stocktwits_src.fetch_cashtag(ticker)
        except Exception as exc:
            log.exception("poller stocktwits failed for %s: %s", ticker, exc)
            out.append(PollSummary(source_id=f"stocktwits:{ticker}", kind="stocktwits", errors=[str(exc)]))
            continue
        out.append(_consume(result, "stocktwits", db))
    return out


def _poll_kind(kind: str, feeds: list[dict], fetcher, db: Session) -> list[PollSummary]:
    out: list[PollSummary] = []
    for feed in feeds:
        sid = feed.get("id") or feed.get("url", "")
        url = feed.get("url", "")
        try:
            result = fetcher(sid, url)
        except Exception as exc:
            log.exception("poller %s failed for %s: %s", kind, sid, exc)
            out.append(PollSummary(source_id=sid, kind=kind, errors=[str(exc)]))
            continue
        out.append(_consume(result, kind, db))
    return out


def _consume(result: SourceFetchResult, kind: str, db: Session) -> PollSummary:
    summary = PollSummary(source_id=result.source_id, kind=kind)
    if result.error:
        summary.errors = [result.error]
        return summary
    if not result.payloads:
        return summary
    summary.fetched = len(result.payloads)
    for payload in result.payloads:
        try:
            res = _emit(db, payload)
            if res.get("signal_id"):
                summary.emitted_signals += 1
        except Exception as exc:
            log.warning("poller %s pipeline error on %s: %s", kind, result.source_id, exc)
            summary.errors = (summary.errors or []) + [str(exc)]
    return summary


def _emit(db: Session, payload: IngestPayload) -> dict[str, Any]:
    """Forward one collector payload into the shared in-process pipeline.

    We re-use _run_pipeline so cross-source confirmation, fake-risk, dedup,
    and signal emission are exactly the same as the HTTP webhook path —
    no behavioural drift between polled and webhook ingestion.
    """
    src = (payload.get("source") or "").lower()
    raw = dict(payload)
    raw["source"] = src
    return _run_pipeline(
        db=db,
        raw_payload=raw,
        author=AuthorMeta(is_known_press=reliability_score(src) >= 80),
        source_id_for_xsrc=src,
    )
