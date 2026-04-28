"""Cross-source confirmation: count distinct sources reporting the same
(ticker, event_type) within a sliding window. Two+ independent sources within
the window meaningfully boost confidence and lower fake_risk.

Backed by Redis when CROSS_SOURCE_REDIS_URL is set; otherwise falls back to an
in-process LRU. The in-process fallback is correct for single-process MVP and
unit tests; multi-worker production should use Redis."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque

from app.config.settings import get_settings

log = logging.getLogger(__name__)

_DEFAULT_WINDOW_SEC = 90


class _InMemoryStore:
    """Simple in-process implementation: per-key deque of (ts, source_id)
    tuples. Older entries are evicted on read."""

    def __init__(self, window_sec: int = _DEFAULT_WINDOW_SEC):
        self._window = window_sec
        self._buckets: dict[str, Deque[tuple[float, str]]] = defaultdict(deque)
        self._lock = Lock()

    def record(self, key: str, source_id: str, ts: float | None = None) -> int:
        ts = ts or time.time()
        cutoff = ts - self._window
        with self._lock:
            dq = self._buckets[key]
            # Skip if the same source already in window
            already = any(s == source_id for _, s in dq if _ > cutoff)
            if not already:
                dq.append((ts, source_id))
            # Evict old
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            distinct = {s for _, s in dq}
            return len(distinct)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class _RedisStore:
    """Redis-backed sliding window using a sorted set per (ticker, event)."""

    def __init__(self, url: str, window_sec: int = _DEFAULT_WINDOW_SEC):
        import redis  # local import; redis is optional at runtime

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._window = window_sec

    def record(self, key: str, source_id: str, ts: float | None = None) -> int:
        ts = ts or time.time()
        cutoff = ts - self._window
        rkey = f"signalx:xsrc:{key}"
        try:
            pipe = self._r.pipeline()
            pipe.zremrangebyscore(rkey, 0, cutoff)
            pipe.zadd(rkey, {source_id: ts})
            pipe.expire(rkey, self._window * 2)
            pipe.zcard(rkey)
            _, _, _, n = pipe.execute()
            return int(n)
        except Exception as exc:  # pragma: no cover
            log.warning("cross_source redis failure key=%s err=%s", key, exc)
            return 1

    def reset(self) -> None:
        try:
            for k in self._r.scan_iter("signalx:xsrc:*"):
                self._r.delete(k)
        except Exception:  # pragma: no cover
            pass


_store: _InMemoryStore | _RedisStore | None = None


def _get_store():
    global _store
    if _store is not None:
        return _store
    s = get_settings()
    window = int(getattr(s, "cross_source_window_sec", _DEFAULT_WINDOW_SEC))
    url = getattr(s, "cross_source_redis_url", None) or s.redis_url
    if url and getattr(s, "cross_source_use_redis", False):
        try:
            _store = _RedisStore(url, window_sec=window)
            return _store
        except Exception as exc:  # pragma: no cover
            log.warning("cross_source falling back to in-memory: %s", exc)
    _store = _InMemoryStore(window_sec=window)
    return _store


def record_observation(ticker: str, event_type: str, source_id: str) -> int:
    """Register that `source_id` published `event_type` for `ticker` and
    return the current distinct-source count within the sliding window."""
    if not ticker or not event_type or not source_id:
        return 1
    return _get_store().record(f"{ticker.upper()}:{event_type.lower()}", source_id)


def reset_for_tests() -> None:
    if _store is not None:
        _store.reset()
