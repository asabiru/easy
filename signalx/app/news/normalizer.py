"""News normalizer — converts raw input from any source to a uniform schema."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


_WS_RE = re.compile(r"\s+")
_HTML_RE = re.compile(r"<[^>]+>")


class NormalizedNews(BaseModel):
    source: str
    source_url: str | None = None
    raw_text: str
    normalized_text: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: datetime | None = None


def _strip_html(text: str) -> str:
    return _HTML_RE.sub(" ", text)


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def normalize_text(text: str) -> str:
    """Lowercase, strip HTML, collapse whitespace, normalize unicode."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _strip_html(text)
    text = text.replace("\u00a0", " ")
    text = _collapse_ws(text)
    return text.lower()


def normalize(payload: dict[str, Any]) -> NormalizedNews:
    """Build a NormalizedNews from a free-form dict (POST body, RSS, etc)."""
    raw_text = (payload.get("raw_text") or payload.get("text") or "").strip()
    if not raw_text:
        raise ValueError("raw_text is required")

    return NormalizedNews(
        source=str(payload.get("source") or "unknown"),
        source_url=payload.get("source_url"),
        raw_text=raw_text,
        normalized_text=normalize_text(raw_text),
        received_at=datetime.utcnow(),
        published_at=_parse_dt(payload.get("published_at")),
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
