"""X (Twitter) v2 filtered-stream collector.

Two operating modes:

  1. **Stream mode** — when `X_STREAM_ENABLED=true` and `X_API_BEARER_TOKEN` is
     set, this module connects to the X API v2 *filtered stream* endpoint
     (https://api.twitter.com/2/tweets/search/stream) with rules built from
     `data/x_sources.json`. Each matching tweet is forwarded to
     `POST /news/ingest/x` on the local FastAPI service.

  2. **Webhook mode (default)** — without a token, this module is a no-op.
     External integrations (Zapier, n8n, IFTTT, scrapers) can post tweets
     directly to `POST /news/ingest/x` and the same pipeline runs.

The collector is intentionally a *standalone* worker (run via
`python -m app.news.x_collector`) rather than tied into the FastAPI process,
so it can be restarted independently without affecting the API."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

STREAM_ENDPOINT = "https://api.twitter.com/2/tweets/search/stream"
RULES_ENDPOINT = "https://api.twitter.com/2/tweets/search/stream/rules"


def _load_x_sources(data_dir: Path) -> dict:
    p = data_dir / "x_sources.json"
    if not p.exists():
        return {"handles": [], "cashtag_filter": []}
    return json.loads(p.read_text(encoding="utf-8"))


def build_rules(sources: dict) -> list[dict]:
    """Build X v2 stream rules from x_sources.json. X allows up to 25 rules
    of 512 chars each on Basic tier; we batch handles into OR groups."""
    handles = [h["handle"] for h in sources.get("handles", [])][:50]
    tags = sources.get("cashtag_filter", [])
    rules: list[dict] = []
    # Rule 1: any of our trusted handles
    if handles:
        from_clause = " OR ".join(f"from:{h}" for h in handles[:25])
        rules.append({"value": f"({from_clause}) -is:retweet lang:en", "tag": "trusted_handles_a"})
        if len(handles) > 25:
            from_clause = " OR ".join(f"from:{h}" for h in handles[25:50])
            rules.append({"value": f"({from_clause}) -is:retweet lang:en", "tag": "trusted_handles_b"})
    # Rule 2: cashtag mentions from any verified account
    if tags:
        cash = " OR ".join(tags[:30])
        rules.append({"value": f"({cash}) is:verified -is:retweet lang:en", "tag": "cashtags"})
    return rules


def _post_tweet_to_pipeline(api_base: str, tweet: dict, includes: dict) -> None:
    """Forward a single tweet object to /news/ingest/x."""
    try:
        import httpx
    except ImportError:  # pragma: no cover
        log.error("httpx not installed; cannot forward X tweet")
        return

    author_id = tweet.get("author_id")
    user = next(
        (u for u in includes.get("users", []) if u.get("id") == author_id),
        {},
    )
    pub_metrics = user.get("public_metrics", {})
    payload = {
        "handle": user.get("username", ""),
        "raw_text": tweet.get("text", ""),
        "tweet_url": f"https://x.com/{user.get('username', '')}/status/{tweet.get('id', '')}",
        "published_at": tweet.get("created_at"),
        "verified": user.get("verified"),
        "followers": pub_metrics.get("followers_count"),
        "account_age_days": _age_days(user.get("created_at")),
        "has_authoritative_link": _has_link(tweet.get("entities", {})),
    }
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(f"{api_base}/news/ingest/x", json=payload)
    except Exception as exc:  # pragma: no cover
        log.warning("forward to /news/ingest/x failed: %s", exc)


def _age_days(created_at: str | None) -> int | None:
    if not created_at:
        return None
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None


def _has_link(entities: dict) -> bool:
    urls = entities.get("urls") or []
    AUTHORITATIVE = (
        "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com",
        "sec.gov", "nasdaq.com", "nyse.com",
    )
    return any(any(d in (u.get("expanded_url") or "") for d in AUTHORITATIVE) for u in urls)


def run_stream(api_base: str, sources: dict, bearer_token: str) -> None:  # pragma: no cover
    """Connect to the X filtered stream and forward each match. Reconnect
    with exponential backoff on transient failures."""
    try:
        import httpx
    except ImportError:
        log.error("httpx not installed; install signalx[x] to enable stream mode")
        return

    headers = {"Authorization": f"Bearer {bearer_token}"}
    rules = build_rules(sources)
    if not rules:
        log.error("no rules built — check data/x_sources.json")
        return

    # Replace existing rules
    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            current = client.get(RULES_ENDPOINT).json()
            ids = [r["id"] for r in (current.get("data") or [])]
            if ids:
                client.post(RULES_ENDPOINT, json={"delete": {"ids": ids}})
            client.post(RULES_ENDPOINT, json={"add": rules})
    except Exception as exc:
        log.error("failed to install X stream rules: %s", exc)
        return

    backoff = 1.0
    params = {
        "expansions": "author_id",
        "tweet.fields": "created_at,entities,public_metrics",
        "user.fields": "username,verified,public_metrics,created_at",
    }
    while True:
        try:
            with httpx.stream(
                "GET", STREAM_ENDPOINT, headers=headers, params=params, timeout=None
            ) as resp:
                resp.raise_for_status()
                backoff = 1.0
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    tweet = evt.get("data") or {}
                    if tweet:
                        _post_tweet_to_pipeline(api_base, tweet, evt.get("includes") or {})
        except Exception as exc:
            log.warning("X stream disconnected: %s; reconnecting in %.1fs", exc, backoff)
            time.sleep(backoff)
            backoff = min(60.0, backoff * 2)


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint. Reads config from env, then either runs the stream
    or exits with a clear message if the prerequisites aren't met."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    enabled = os.environ.get("X_STREAM_ENABLED", "").lower() in ("1", "true", "yes")
    token = os.environ.get("X_API_BEARER_TOKEN", "").strip()
    api_base = os.environ.get("SIGNALX_API_BASE", "http://localhost:8000")

    data_dir_env = os.environ.get("DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else (
        Path(__file__).resolve().parent.parent.parent / "data"
    )
    sources = _load_x_sources(data_dir)

    if not enabled or not token:
        log.warning(
            "X_STREAM_ENABLED=%s X_API_BEARER_TOKEN=%s — exiting. "
            "Webhook ingest at POST /news/ingest/x is still active.",
            enabled, "<set>" if token else "<empty>",
        )
        return 0

    log.info("Starting X filtered stream collector → %s", api_base)
    run_stream(api_base, sources, token)  # pragma: no cover
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
