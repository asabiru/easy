"""Configuration loader for `data/feeds.json`."""
from __future__ import annotations

import json
from functools import lru_cache

from app.config.settings import get_settings


@lru_cache(maxsize=1)
def load_feeds() -> dict:
    path = get_settings().data_dir / "feeds.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def rss_feeds() -> list[dict]:
    return list(load_feeds().get("rss", []))


def edgar_feeds() -> list[dict]:
    return list(load_feeds().get("sec_edgar", []))


def macro_energy_feeds() -> list[dict]:
    return list(load_feeds().get("macro_energy", []))


def reddit_feeds() -> list[dict]:
    return list(load_feeds().get("reddit", []))


def mastodon_feeds() -> list[dict]:
    return list(load_feeds().get("mastodon", []))


def stocktwits_config() -> dict:
    """StockTwits collector config: enabled, cadence, symbol allow-list.

    Returns the raw object from feeds.json so callers can read both
    `enabled` and `endpoint`. Polled symbols come from companies.json
    (via the auto-trade universe) — only equities are polled, crypto
    tickers are skipped.
    """
    return dict(load_feeds().get("stocktwits", {}))


def bluesky_feeds() -> list[dict]:
    return list(load_feeds().get("bluesky", []))


def all_reliability() -> dict[str, int]:
    """Flat map of source_id → reliability for every configured feed.
    The source_reliability module folds these into the same lookup as
    sources.json / x_sources.json."""
    out: dict[str, int] = {}
    feeds = load_feeds()
    for kind in ("rss", "sec_edgar", "macro_energy", "reddit", "mastodon", "bluesky"):
        for f in feeds.get(kind, []):
            sid = f.get("id")
            if sid:
                out[sid.lower()] = int(f.get("reliability", 30))
    return out
