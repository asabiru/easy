"""Per-IP rate limiter — small in-memory token-bucket.

Designed for endpoints where abuse is cheap and damage is real:
  - /referral/me (would let an attacker enumerate codes)
  - /autotrade/{id}/test-keys (pings real exchanges, costs API quota)

Single-process only. For multi-worker / fly-machine deploys, swap the
backend for Redis (pattern stays identical — `RateLimiter.allow()` is the
only call site).

Usage:

    rl_referral = RateLimiter("referral_me", per_ip_per_min=10)

    @router.get("/referral/me")
    def my_referral(_=Depends(rl_referral), ...):
        ...

The dependency raises ``429 Too Many Requests`` on overflow with a
``Retry-After`` header. Tests can disable by setting
``RATE_LIMIT_ENABLED=false`` in the environment.
"""
import os
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding-window per-IP rate limiter."""

    def __init__(self, name: str, per_ip_per_min: int) -> None:
        self.name = name
        self.cap = max(1, per_ip_per_min)
        self.window = 60.0
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)

    def _enabled(self) -> bool:
        return os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"

    def _client_ip(self, req: Request) -> str:
        # respect Fly-Forwarded-For but fall back to direct peer
        fwd = req.headers.get("fly-client-ip") or req.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return req.client.host if req.client else "unknown"

    def __call__(self, request: Request) -> None:
        if not self._enabled():
            return
        ip = self._client_ip(request)
        now = time.monotonic()
        bucket = self._buckets[ip]
        # Drop stale timestamps. O(k) where k = max(cap, len(bucket)) — bounded.
        while bucket and bucket[0] < now - self.window:
            bucket.popleft()
        if len(bucket) >= self.cap:
            retry = max(1, int(self.window - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded for {self.name}",
                headers={"Retry-After": str(retry)},
            )
        bucket.append(now)


# Pre-built instances (keep here so they're singletons across the process).
referral_limiter = RateLimiter("referral_me", per_ip_per_min=20)
test_keys_limiter = RateLimiter("test_keys", per_ip_per_min=10)
