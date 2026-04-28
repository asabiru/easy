"""News collectors. MVP v0.1 ships:
  - manual ingest via POST /news/ingest (handled in api.routes_news)
  - mock collector for tests/dev
  - RSS skeleton (feedparser)
  - Telegram skeleton (placeholder)
"""
from __future__ import annotations

import logging
from typing import Iterable

import feedparser  # type: ignore[import-untyped]

from app.news.normalizer import NormalizedNews, normalize

log = logging.getLogger(__name__)


def mock_collect() -> list[NormalizedNews]:
    """Static news for dev/tests."""
    samples = [
        {
            "source": "manual_test",
            "source_url": "https://example.com/nvda-guidance",
            "raw_text": "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand.",
        },
        {
            "source": "manual_test",
            "source_url": "https://example.com/tsla-recall",
            "raw_text": "Tesla announces voluntary recall of Cybertruck over accelerator defect.",
        },
    ]
    return [normalize(s) for s in samples]


def rss_collect(feed_urls: Iterable[str]) -> list[NormalizedNews]:
    """Skeleton: pull headlines from RSS. Errors per-feed are swallowed."""
    out: list[NormalizedNews] = []
    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:50]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                link = getattr(entry, "link", None)
                text = f"{title}. {summary}".strip(" .")
                if not text:
                    continue
                out.append(
                    normalize(
                        {
                            "source": "rss",
                            "source_url": link,
                            "raw_text": text,
                            "published_at": getattr(entry, "published", None),
                        }
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("RSS feed failed url=%s err=%s", url, exc)
    return out


def telegram_collect() -> list[NormalizedNews]:
    """Skeleton: in MVP v0.1 we only post via Telegram, not consume.
    Future: wire a Telethon/aiogram client to read configured channels.
    """
    return []
