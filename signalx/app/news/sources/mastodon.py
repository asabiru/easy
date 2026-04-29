"""Mastodon hashtag timeline collector.

Mastodon instances expose unauthenticated public hashtag timelines at:
  https://<instance>/api/v1/timelines/tag/<tag>?limit=40

We fetch JSON, filter out boosts/replies, dedup by status id, and emit
IngestPayload entries."""
from __future__ import annotations

import logging
import re

import httpx

from app.news.sources import state
from app.news.sources.types import IngestPayload, SourceFetchResult

log = logging.getLogger(__name__)

USER_AGENT = "SignalXNewsBot/0.1"
HTTP_TIMEOUT = 5.0
_HTML = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML.sub(" ", s or "").strip()


def fetch_tag(source_id: str, instance: str, tag: str) -> SourceFetchResult:
    url = f"{instance.rstrip('/')}/api/v1/timelines/tag/{tag}?limit=40"
    headers = {"User-Agent": USER_AGENT, **state.cache_headers(source_id)}
    try:
        resp = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    except Exception as exc:
        return SourceFetchResult(source_id=source_id, error=f"http error: {exc}")
    if resp.status_code >= 400:
        return SourceFetchResult(source_id=source_id, error=f"http {resp.status_code}")

    try:
        body = resp.json()
    except Exception as exc:
        return SourceFetchResult(source_id=source_id, error=f"json parse: {exc}")

    payloads: list[IngestPayload] = []
    for status in body[:40]:
        if not isinstance(status, dict):
            continue
        if status.get("reblog") is not None:
            continue  # skip boosts; the original will appear from elsewhere
        sid = str(status.get("id", ""))
        if not sid or state.is_seen(source_id, sid):
            continue
        text = _strip_html(status.get("content", ""))
        if not text:
            continue
        url_ = status.get("url") or status.get("uri")
        payloads.append({
            "source": source_id,
            "source_url": url_,
            "raw_text": text[:1500],
            "published_at": status.get("created_at"),
        })
        state.mark_seen(source_id, sid)

    return SourceFetchResult(source_id=source_id, payloads=payloads)
