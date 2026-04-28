"""SEC EDGAR ATOM feed collector.

EDGAR exposes "current filings" feeds at:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom

Each entry's title is shaped like:
  "8-K - APPLE INC (0000320193) (Filer)"

We extract the form type and the issuer name (the bit before the first CIK
parenthetical) and synthesize a `raw_text` that hits our keyword-based
company mapper cleanly.

EDGAR is rate-limited; honor the `User-Agent` requirement (must include a
contact email — set via SEC_EDGAR_USER_AGENT env var)."""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

import feedparser
import httpx

from app.news.sources import state
from app.news.sources.types import IngestPayload, SourceFetchResult

log = logging.getLogger(__name__)

DEFAULT_UA = "SignalXNewsBot/0.1 (signalx-research@example.com)"
HTTP_TIMEOUT = 5.0

_TITLE_RE = re.compile(r"^([A-Z0-9-]+)\s*-\s*(.+?)\s*\(\d+\)\s*\(.+?\)\s*$", re.IGNORECASE)


def _parse_title(title: str) -> tuple[str | None, str | None]:
    """Return (form, issuer_name) or (None, None) if title is unparseable."""
    if not title:
        return None, None
    m = _TITLE_RE.match(title.strip())
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2).strip()


def _form_to_event_text(form: str, issuer: str) -> str:
    f = form.upper()
    if f == "8-K":
        return f"{issuer} files 8-K with the SEC."
    if f == "10-Q":
        return f"{issuer} files quarterly 10-Q with the SEC."
    if f == "10-K":
        return f"{issuer} files annual 10-K with the SEC."
    if f == "13D":
        return f"{issuer} subject to 13D filing — beneficial-owner stake disclosed."
    if f == "13G":
        return f"{issuer} subject to 13G filing — passive stake disclosed."
    if f == "S-1":
        return f"{issuer} files S-1 registration statement with the SEC."
    if f == "DEF 14A":
        return f"{issuer} files DEF 14A proxy statement."
    return f"{issuer} files {form} with the SEC."


def fetch_edgar(source_id: str, url: str, user_agent: str | None = None) -> SourceFetchResult:
    ua = user_agent or os.environ.get("SEC_EDGAR_USER_AGENT") or DEFAULT_UA
    headers = {"User-Agent": ua, "Accept": "application/atom+xml", **state.cache_headers(source_id)}
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
        resp.headers.get("ETag"),
        resp.headers.get("Last-Modified"),
    )

    parsed = feedparser.parse(resp.content)
    payloads: list[IngestPayload] = []
    for entry in parsed.entries[:40]:
        item_id = entry.get("id") or entry.get("link") or ""
        if not item_id or state.is_seen(source_id, item_id):
            continue
        title = entry.get("title", "")
        form, issuer = _parse_title(title)
        if not issuer or not form:
            continue
        payloads.append({
            "source": f"sec:{source_id}",
            "source_url": entry.get("link"),
            "raw_text": _form_to_event_text(form, issuer),
            "published_at": None,
        })
        state.mark_seen(source_id, item_id)

    return SourceFetchResult(source_id=source_id, payloads=payloads)


def fetch_all_configured(feeds: Iterable[dict]) -> list[SourceFetchResult]:
    out: list[SourceFetchResult] = []
    for feed in feeds:
        out.append(fetch_edgar(feed["id"], feed["url"]))
    return out
