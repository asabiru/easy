"""Lightweight sentiment refinement on top of the rule classifier.

MVP v0.1 uses a tiny lexicon to nudge `unclear` cases into bullish/bearish/neutral.
Future: swap to a transformer model.
"""
from __future__ import annotations

from app.analysis.classifier import EventClassification


_BULLISH = {
    "beat", "beats", "raises", "raised", "surge", "surges", "record", "strong",
    "above expectations", "tops", "growth", "expands", "wins", "approved",
}
_BEARISH = {
    "miss", "misses", "cuts", "downgrade", "downgrades", "fall", "falls",
    "plunge", "plunges", "below expectations", "loss", "losses", "sued",
    "lawsuit", "investigation", "fraud", "recall", "halt", "ban",
}


def refine(classification: EventClassification, normalized_text: str) -> EventClassification:
    if classification.direction in ("bullish", "bearish"):
        return classification

    text = (normalized_text or "").lower()
    bull = sum(1 for w in _BULLISH if w in text)
    bear = sum(1 for w in _BEARISH if w in text)

    if bull == 0 and bear == 0:
        return EventClassification(
            event_type=classification.event_type,
            direction="neutral",
            base_impact=classification.base_impact,
            matched_keywords=classification.matched_keywords,
            confidence=max(30.0, classification.confidence * 0.6),
        )

    direction = "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")
    confidence = min(80.0, 40.0 + abs(bull - bear) * 10.0)
    impact = max(classification.base_impact, 30 if direction != "neutral" else classification.base_impact)
    return EventClassification(
        event_type=classification.event_type or "other",
        direction=direction,
        base_impact=impact,
        matched_keywords=classification.matched_keywords,
        confidence=confidence,
    )
