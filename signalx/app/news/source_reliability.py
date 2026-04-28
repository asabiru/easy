"""Source reliability lookup, including X (Twitter) handles loaded from
data/x_sources.json."""
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


@lru_cache(maxsize=1)
def _x_handles() -> dict[str, dict]:
    """Map of lowercased X handle → record from data/x_sources.json."""
    path = get_settings().data_dir / "x_sources.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for row in payload.get("handles", []):
        h = row.get("handle", "").lstrip("@").lower()
        if h:
            out[h] = row
    return out


def reliability_score(source: str) -> int:
    """0..100. Unknown source → 30. Recognises both press sources (sources.json)
    and known X handles (x_sources.json) — pass `x:elonmusk` or just
    `elonmusk`. The 'x:' prefix is stripped before lookup."""
    if not source:
        return 30
    key = source.lower()
    if key.startswith("x:"):
        key = key[2:]
    row = _sources().get(key)
    if row:
        return int(row.get("reliability", 30))
    xrow = _x_handles().get(key.lstrip("@"))
    if xrow:
        return int(xrow.get("reliability", 30))
    return 30


def source_type(source: str) -> str:
    if not source:
        return "unknown"
    key = source.lower()
    if key.startswith("x:"):
        key = key[2:]
    row = _sources().get(key)
    if row:
        return str(row.get("type", "unknown"))
    xrow = _x_handles().get(key.lstrip("@"))
    if xrow:
        return f"x_{xrow.get('tier', 'unknown')}"
    return "unknown"


def x_handle_meta(handle: str) -> dict | None:
    """Return the x_sources.json record for a handle, or None."""
    if not handle:
        return None
    return _x_handles().get(handle.lstrip("@").lower())
