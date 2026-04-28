"""Source reliability lookup."""
from __future__ import annotations

import json
from functools import lru_cache

from app.config.settings import get_settings


@lru_cache(maxsize=1)
def _sources() -> dict[str, dict]:
    path = get_settings().data_dir / "sources.json"
    with path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    return {row["source"].lower(): row for row in rows}


def reliability_score(source: str) -> int:
    """0..100. Unknown source → 30."""
    if not source:
        return 30
    row = _sources().get(source.lower())
    if not row:
        return 30
    return int(row.get("reliability", 30))


def source_type(source: str) -> str:
    row = _sources().get((source or "").lower())
    return str(row.get("type", "unknown")) if row else "unknown"
