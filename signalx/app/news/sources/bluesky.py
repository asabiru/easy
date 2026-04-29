"""BlueSky / AT-protocol collector — webhook-only for MVP.

Stream support requires a BlueSky API token; until that's configured this
module exposes only the webhook payload conversion that the API endpoint
uses. Mirrors the X webhook shape so /news/ingest/x can be reused with a
`bsky:` source prefix."""
from __future__ import annotations

from typing import Any

from app.news.sources.types import IngestPayload


def webhook_to_payload(post: dict[str, Any]) -> IngestPayload:
    handle = (post.get("author") or {}).get("handle", "")
    text = (post.get("record") or {}).get("text", "")
    return {
        "source": f"bsky:{handle.lower()}",
        "source_url": post.get("uri"),
        "raw_text": text,
        "published_at": (post.get("record") or {}).get("createdAt"),
    }
