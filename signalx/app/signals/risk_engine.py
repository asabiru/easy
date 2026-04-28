"""Risk Engine — gates whether a signal can be emitted as a tradeable action.

Inputs (all optional → conservative defaults):
  - impact_score
  - confidence
  - spread (bps)
  - volume_5m
  - price_change_5m
  - source_reliability

Outputs (RiskAssessment): action_override, risk_level, reasons[]
where action_override ∈ {None, "WATCH", "SKIP"}.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.market.liquidity import has_late_entry_risk, is_liquid
from app.market.spread import is_spread_acceptable


@dataclass
class RiskAssessment:
    action_override: str | None = None  # None | WATCH | SKIP
    risk_level: str = "low"  # low | medium | high
    reasons: list[str] = field(default_factory=list)
    spread_risk: int = 0
    liquidity_risk: int = 0
    late_entry_risk: int = 0
    fake_news_risk: int = 0


def assess(
    *,
    impact_score: float,
    confidence: float,
    spread_bps: float | None,
    volume_5m: float | None,
    price_change_5m: float | None,
    source_reliability: int,
) -> RiskAssessment:
    a = RiskAssessment()

    if not is_spread_acceptable(spread_bps):
        a.spread_risk = 25
        a.reasons.append(f"spread too wide ({spread_bps} bps)")
        a.action_override = "SKIP"

    if not is_liquid(volume_5m):
        a.liquidity_risk = 20
        a.reasons.append(f"low 5m volume ({volume_5m})")
        a.action_override = "SKIP"

    if has_late_entry_risk(price_change_5m):
        a.late_entry_risk = 15
        a.reasons.append(f"price already moved {price_change_5m}% in 5m")
        if a.action_override != "SKIP":
            a.action_override = "WATCH"

    if source_reliability < 50:
        a.fake_news_risk = 15
        a.reasons.append(f"unreliable source (reliability={source_reliability})")
        if a.action_override != "SKIP":
            a.action_override = "WATCH"

    if confidence < 60:
        a.reasons.append(f"low confidence ({confidence})")
        if a.action_override != "SKIP":
            a.action_override = "WATCH"

    if impact_score < 60:
        a.reasons.append(f"low impact_score ({impact_score})")
        if a.action_override is None:
            a.action_override = "WATCH"

    # Risk level
    risk_points = a.spread_risk + a.liquidity_risk + a.late_entry_risk + a.fake_news_risk
    if risk_points >= 40 or a.action_override == "SKIP":
        a.risk_level = "high"
    elif risk_points >= 15 or a.action_override == "WATCH":
        a.risk_level = "medium"
    else:
        a.risk_level = "low"

    return a
