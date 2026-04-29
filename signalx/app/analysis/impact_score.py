"""Impact score (0..100) for a classified news event.

Final impact_score = clamp(base_impact + adjustments, 0, 100).
Adjustments:
  + 5  if multiple keyword matches reinforce the event
  + 5  if direction is decisive (bullish/bearish, not neutral/unclear)
  - 10 if direction is unclear
"""
from __future__ import annotations

from app.analysis.classifier import EventClassification


def compute_impact(c: EventClassification) -> int:
    score = float(c.base_impact)
    if len(c.matched_keywords) >= 2:
        score += 5
    if c.direction in ("bullish", "bearish"):
        score += 5
    if c.direction == "unclear":
        score -= 10
    score = max(0.0, min(100.0, score))
    return int(round(score))
