"""Reddit subreddit RSS feed collector.

Reddit serves a public RSS at https://www.reddit.com/r/<sub>/.rss; no auth
required. Reliability is intentionally low (30–40) so signals from Reddit
alone are pinned to WATCH/SKIP, but cross-source confirmation can promote
them when a press wire confirms the same event."""
from __future__ import annotations

import logging

from app.news.sources.rss import fetch_rss
from app.news.sources.types import SourceFetchResult

log = logging.getLogger(__name__)


def fetch_subreddit(source_id: str, url: str) -> SourceFetchResult:
    """Reddit RSS is shaped exactly like other Atom feeds, so we just defer."""
    return fetch_rss(source_id, url)
