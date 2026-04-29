"""StockTwits cashtag stream poller.

StockTwits exposes per-symbol JSON streams without auth at:
  https://api.stocktwits.com/api/2/streams/symbol/<TICKER>.json

Reliability is intentionally low (40) — most posts are retail commentary —
but they're often *first* with rumor / price-action color. The cross-source
window then filters the chaff: a StockTwits-only post stays at WATCH, but
a press wire confirming the same event-type within 90s promotes it.
"""
from __future__ import annotations

import logging

import httpx

from app.news.sources import state
from app.news.sources.types import IngestPayload, SourceFetchResult

log = logging.getLogger(__name__)

ENDPOINT = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
USER_AGENT = "SignalXNewsBot/0.1"
HTTP_TIMEOUT = 5.0


def fetch_cashtag(symbol: str) -> SourceFetchResult:
    """Poll one StockTwits symbol stream."""
    source_id = f"stocktwits:{symbol.upper()}"
    url = ENDPOINT.format(symbol=symbol.upper())
    headers = {"User-Agent": USER_AGENT, **state.cache_headers(source_id)}
    try:
        resp = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    except Exception as exc:
        return SourceFetchResult(source_id=source_id, error=f"http error: {exc}")

    if resp.status_code == 429:  # rate-limited, just bail quietly
        return SourceFetchResult(source_id=source_id, error="rate-limited")
    if resp.status_code >= 400:
        return SourceFetchResult(source_id=source_id, error=f"http {resp.status_code}")

    try:
        body = resp.json()
    except Exception as exc:
        return SourceFetchResult(source_id=source_id, error=f"json parse: {exc}")

    payloads: list[IngestPayload] = []
    for msg in body.get("messages", [])[:40]:
        mid = str(msg.get("id"))
        if not mid or state.is_seen(source_id, mid):
            continue
        text = msg.get("body") or ""
        if not text:
            continue
        payloads.append({
            "source": source_id,
            "source_url": f"https://stocktwits.com/message/{mid}",
            "raw_text": text[:1500],
            "published_at": msg.get("created_at"),
        })
        state.mark_seen(source_id, mid)

    return SourceFetchResult(source_id=source_id, payloads=payloads)
