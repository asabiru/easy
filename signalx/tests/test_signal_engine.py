"""Signal engine integration tests."""
from app.analysis.classifier import classify
from app.analysis.impact_score import compute_impact
from app.analysis.sentiment import refine
from app.companies.mapper import find_company
from app.market.exchange_client import get_exchange_client
from app.news.source_reliability import reliability_score
from app.signals.signal_engine import decide


def _pipeline(text: str, source: str = "reuters"):
    match = find_company(text.lower())
    assert match is not None, f"company not matched for: {text}"
    c = refine(classify(text.lower()), text.lower())
    impact = compute_impact(c)
    market = get_exchange_client().fetch_market_data(match.company.exchange_symbol)
    src_rel = reliability_score(source)
    return decide(
        classification=c,
        impact_score=impact,
        company_ticker=match.company.ticker,
        symbol=match.company.exchange_symbol,
        market=market,
        source_reliability=src_rel,
    )


def test_nvda_guidance_raised_yields_long():
    d = _pipeline(
        "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand."
    )
    assert d.ticker == "NVDA"
    assert d.symbol == "NVDAUSDT"
    assert d.direction == "bullish"
    assert d.action in ("LONG", "WATCH")  # depends on mock spread; mock is liquid → LONG
    assert d.action == "LONG"
    assert d.impact_score >= 80
    assert d.signal_score >= 70


def test_tesla_recall_yields_short():
    d = _pipeline(
        "Tesla announces voluntary recall of Cybertruck over accelerator defect."
    )
    assert d.ticker == "TSLA"
    assert d.direction == "bearish"
    assert d.action in ("SHORT", "WATCH")


def test_unreliable_source_demotes_to_watch():
    d = _pipeline(
        "Nvidia raises Q2 revenue guidance above Wall Street expectations after strong AI chip demand.",
        source="twitter",
    )
    # twitter reliability=40 → risk engine forces WATCH
    assert d.action == "WATCH"


def test_signal_serializable():
    d = _pipeline("Nvidia raises Q2 revenue guidance above Wall Street expectations.")
    out = d.to_dict()
    assert set(out).issuperset(
        {"action", "ticker", "symbol", "direction", "impact_score", "confidence",
         "signal_score", "risk_level", "reason", "max_holding_minutes"}
    )
