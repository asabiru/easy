"""Event-type classifier using rule-based keyword matching from event_rules.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.config.settings import get_settings


@dataclass
class EventClassification:
    event_type: str  # e.g. earnings_beat, guidance_raised, ...
    direction: str  # bullish | bearish | neutral | unclear
    base_impact: int  # 0..100 from rule
    matched_keywords: list[str]
    confidence: float  # 0..100


@lru_cache(maxsize=1)
def _rules() -> dict[str, dict]:
    path = get_settings().data_dir / "event_rules.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def classify(normalized_text: str) -> EventClassification:
    if not normalized_text:
        return EventClassification("other", "unclear", 0, [], 0.0)

    text = normalized_text.lower()
    rules = _rules()

    best_type = "other"
    best_dir = "unclear"
    best_impact = 0
    best_kw: list[str] = []
    best_count = 0

    for event_type, rule in rules.items():
        matched = [kw for kw in rule.get("keywords", []) if kw.lower() in text]
        if not matched:
            continue
        count = len(matched)
        impact = int(rule.get("impact", 0))
        # rank by (impact, count) so high-impact rules dominate
        if (impact, count) > (best_impact, best_count):
            best_type = event_type
            best_dir = rule.get("direction", "unclear")
            best_impact = impact
            best_kw = matched
            best_count = count

    if best_type == "other":
        return EventClassification("other", "unclear", 0, [], 20.0)

    # Confidence: scaled by number of matches and rule impact strength.
    confidence = min(95.0, 50.0 + best_count * 10.0 + best_impact * 0.2)
    return EventClassification(best_type, best_dir, best_impact, best_kw, confidence)
