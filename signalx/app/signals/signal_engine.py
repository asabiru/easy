"""Signal Engine — combines news classification, market data, source reliability,
and risk assessment into a final action.

Scoring formula:
  final_score = impact_score
              + source_reliability_score
              + market_confirmation_score
              - spread_risk
              - liquidity_risk
              - late_entry_risk
              - fake_news_risk

Buckets:
  0–39   SKIP
  40–59  WATCH
  60–74  weak signal     (LONG/SHORT)
  75–89  strong signal   (LONG/SHORT)
  90+    very strong     (LONG/SHORT)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.analysis.classifier import EventClassification
from app.market.exchange_client import MarketData
from app.signals.risk_engine import RiskAssessment, assess


@dataclass
class SignalDecision:
    action: str  # LONG | SHORT | WATCH | SKIP
    ticker: str
    symbol: str
    direction: str
    impact_score: int
    confidence: int
    signal_score: int
    risk_level: str
    reason: str
    max_holding_minutes: int
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def _market_confirmation(direction: str, price_change_5m: float | None) -> int:
    """+10 if 5m move agrees with direction, -5 if it disagrees, 0 if unknown."""
    if price_change_5m is None or direction not in ("bullish", "bearish"):
        return 0
    if direction == "bullish" and price_change_5m > 0:
        return 10
    if direction == "bearish" and price_change_5m < 0:
        return 10
    return -5


def _bucket_action(direction: str, score: int, override: str | None) -> str:
    if override == "SKIP":
        return "SKIP"
    if override == "WATCH":
        return "WATCH"
    if score < 40:
        return "SKIP"
    if score < 60:
        return "WATCH"
    if direction == "bullish":
        return "LONG"
    if direction == "bearish":
        return "SHORT"
    return "WATCH"


def _stops(direction: str, price: float | None) -> tuple[float | None, float | None]:
    """Conservative defaults: 0.7% SL / 1.4% TP (2:1 R/R)."""
    if price is None or direction not in ("bullish", "bearish"):
        return None, None
    if direction == "bullish":
        return round(price * 0.993, 4), round(price * 1.014, 4)
    return round(price * 1.007, 4), round(price * 0.986, 4)


def decide(
    *,
    classification: EventClassification,
    impact_score: int,
    company_ticker: str,
    symbol: str,
    market: MarketData,
    source_reliability: int,
    fake_risk: int = 0,
    confirmation_count: int = 1,
) -> SignalDecision:
    direction = classification.direction
    confidence = int(round(classification.confidence))

    # Cross-source confirmation boost: 2nd independent source nudges confidence,
    # 3rd+ gives a meaningful bump. Capped to avoid runaway sock-puppet inflation.
    if confirmation_count >= 3:
        confidence = min(100, confidence + 15)
    elif confirmation_count == 2:
        confidence = min(100, confidence + 8)

    risk: RiskAssessment = assess(
        impact_score=impact_score,
        confidence=confidence,
        spread_bps=market.spread,
        volume_5m=market.volume_5m,
        price_change_5m=market.price_change_5m,
        source_reliability=source_reliability,
        fake_risk=fake_risk,
    )

    market_conf = _market_confirmation(direction, market.price_change_5m)

    score = (
        impact_score
        + int(source_reliability * 0.2)  # reliability contributes up to +20
        + market_conf
        - risk.spread_risk
        - risk.liquidity_risk
        - risk.late_entry_risk
        - risk.fake_news_risk
    )
    score = max(0, min(100, score))

    action = _bucket_action(direction, score, risk.action_override)
    sl, tp = _stops(direction, market.price)
    if action in ("WATCH", "SKIP"):
        sl = tp = None

    reason_bits: list[str] = []
    if classification.event_type and classification.event_type != "other":
        reason_bits.append(f"event={classification.event_type}")
    if classification.matched_keywords:
        reason_bits.append("kw=" + ",".join(classification.matched_keywords[:3]))
    if market.price_change_5m is not None:
        reason_bits.append(f"5m_move={market.price_change_5m}")
    reason_bits.extend(risk.reasons)
    reason = "; ".join(reason_bits) or "n/a"

    return SignalDecision(
        action=action,
        ticker=company_ticker,
        symbol=symbol,
        direction=direction,
        impact_score=impact_score,
        confidence=confidence,
        signal_score=score,
        risk_level=risk.risk_level,
        reason=reason,
        max_holding_minutes=15,
        entry_price=market.price,
        stop_loss=sl,
        take_profit=tp,
    )
