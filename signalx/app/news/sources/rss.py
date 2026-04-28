"""Generic RSS / Atom feed collector.

Designed to be cheap to call repeatedly: ETag / If-Modified-Since cache from
state.py prevents wasted bandwidth, and per-feed `seen_ids` dedup means we
only emit *new* items. Each new entry is converted to the same IngestPayload
shape as `POST /news/ingest`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser
import httpx

from app.news.sources import state
from app.news.sources.types import IngestPayload, SourceFetchResult

log = logging.getLogger(__name__)

USER_AGENT = "SignalXNewsBot/0.1 (+https://github.com/asabiru/easy)"
HTTP_TIMEOUT = 5.0


def fetch_rss(source_id: str, url: str) -> SourceFetchResult:
    """Poll one RSS / Atom feed and return only entries we haven't seen."""
    headers = {"User-Agent": USER_AGENT, **state.cache_headers(source_id)}
    try:
        resp = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT, follow_redirects=True)
    except Exception as exc:
        return SourceFetchResult(source_id=source_id, error=f"http error: {exc}")

    if resp.status_code == 304:
        return SourceFetchResult(source_id=source_id)
    if resp.status_code >= 400:
        return SourceFetchResult(
            source_id=source_id, error=f"http {resp.status_code}"
        )

    state.update_cache(
        source_id,
        resp.headers.get("ETag") or resp.headers.get("etag"),
        resp.headers.get("Last-Modified") or resp.headers.get("last-modified"),
    )

    parsed = feedparser.parse(resp.content)
    payloads: list[IngestPayload] = []
    for entry in parsed.entries[:50]:
        item_id = (
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
            or entry.get("title", "")
        )
        if not item_id or state.is_seen(source_id, item_id):
            continue
        text = _entry_to_text(entry)
        if not text:
            continue
        payloads.append({
            "source": source_id,
            "source_url": entry.get("link"),
            "raw_text": text,
            "published_at": _entry_published_at(entry),
        })
        state.mark_seen(source_id, item_id)

    return SourceFetchResult(
        source_id=source_id,
        payloads=payloads,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
    )


def _entry_to_text(entry: dict) -> str:
    """Title + first ~280 chars of summary keeps signal density high without
    bloating dedup hashes."""
    title = (entry.get("title") or "").strip()
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    if not title and not summary:
        return ""
    body = title
    if summary and summary != title:
        body = f"{title}. {summary}" if title else summary
    return body[:1500]


def _entry_published_at(entry: dict):
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        v = entry.get(k)
        if v:
            try:
                return datetime(*v[:6], tzinfo=timezone.utc).replace(tzinfo=None)
            except Exception:
                pass
    return None


def fetch_all_configured(feeds: list[dict]) -> list[SourceFetchResult]:
    """Convenience: poll a list of feed configs sequentially and return the
    raw results. The caller (Celery beat task) is responsible for forwarding
    payloads to the pipeline."""
    out: list[SourceFetchResult] = []
    for feed in feeds:
        out.append(fetch_rss(feed["id"], feed["url"]))
    return out
