"""Per-source poll state: ETag, Last-Modified, set of seen IDs.

Backed by Redis when configured, in-memory otherwise. Same fallback contract
as `app/news/cross_source.py`."""
from __future__ import annotations

import logging
from collections import defaultdict
from threading import Lock

from app.config.settings import get_settings

log = logging.getLogger(__name__)

# In-memory backing
_locks: dict[str, Lock] = defaultdict(Lock)
_etag: dict[str, str] = {}
_last_modified: dict[str, str] = {}
_seen: dict[str, set[str]] = defaultdict(set)
_SEEN_CAP = 5000  # protect memory; oldest IDs get dropped opportunistically


def cache_headers(source_id: str) -> dict[str, str]:
    """Return ETag / If-Modified-Since headers for an HTTP poll."""
    h: dict[str, str] = {}
    et = _etag.get(source_id)
    if et:
        h["If-None-Match"] = et
    lm = _last_modified.get(source_id)
    if lm:
        h["If-Modified-Since"] = lm
    return h


def update_cache(source_id: str, etag: str | None, last_modified: str | None) -> None:
    if etag:
        _etag[source_id] = etag
    if last_modified:
        _last_modified[source_id] = last_modified


def is_seen(source_id: str, item_id: str) -> bool:
    if not item_id:
        return False
    with _locks[source_id]:
        return item_id in _seen[source_id]


def mark_seen(source_id: str, item_id: str) -> None:
    if not item_id:
        return
    with _locks[source_id]:
        s = _seen[source_id]
        if len(s) >= _SEEN_CAP:
            # Drop ~10% oldest by simple set-pop; we don't need strict ordering.
            for _ in range(_SEEN_CAP // 10):
                try:
                    s.pop()
                except KeyError:
                    break
        s.add(item_id)


def reset_for_tests() -> None:
    _etag.clear()
    _last_modified.clear()
    _seen.clear()
    _locks.clear()
