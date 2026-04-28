"""News deduplicator — hash + fuzzy match against recent items."""
from __future__ import annotations

import hashlib
from collections import deque
from typing import Deque

from rapidfuzz import fuzz


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class Deduplicator:
    """In-memory deduplicator with rolling window of recent normalized texts.

    For DB-backed dedup, see `app.api.routes_news` which also checks
    `text_hash` against the news_events table.
    """

    def __init__(self, window: int = 500, fuzzy_threshold: int = 92) -> None:
        self.window = window
        self.fuzzy_threshold = fuzzy_threshold
        self._recent: Deque[tuple[str, str]] = deque(maxlen=window)  # (hash, text)

    def is_duplicate(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        h = text_hash(normalized_text)
        for prev_hash, prev_text in self._recent:
            if h == prev_hash:
                return True
            if fuzz.token_set_ratio(prev_text, normalized_text) >= self.fuzzy_threshold:
                return True
        self._recent.append((h, normalized_text))
        return False


_default = Deduplicator()


def is_duplicate(normalized_text: str) -> bool:
    return _default.is_duplicate(normalized_text)
