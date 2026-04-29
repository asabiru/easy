"""Shared types for news source collectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict


class IngestPayload(TypedDict, total=False):
    """Shape accepted by `POST /news/ingest`."""

    source: str
    source_url: str
    raw_text: str
    published_at: datetime | str | None


@dataclass
class SourceFetchResult:
    """Output of one collector poll cycle. `payloads` are ready to be POST'd
    to the ingest endpoint; `meta` carries cache hints (etag / last_modified)
    so the next poll can be cheap."""

    source_id: str
    payloads: list[IngestPayload] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
