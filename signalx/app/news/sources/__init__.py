"""Multi-source news collectors.

Every collector exposes the same interface:

  fetch_payloads(now: datetime | None = None) -> list[IngestPayload]

where IngestPayload is the dict shape accepted by `POST /news/ingest`.

Collectors are stateless w.r.t. caller: state (ETag, last_modified, last_seen
ids) is kept in `app/news/sources/state.py` so the same collector can be
invoked safely from Celery beat, a CLI, or a test."""
from app.news.sources.types import IngestPayload, SourceFetchResult  # noqa: F401
