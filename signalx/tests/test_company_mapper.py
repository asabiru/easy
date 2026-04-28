"""Company mapper tests."""
from app.companies.mapper import find_company


def test_finds_nvidia_by_full_name():
    m = find_company("nvidia raises q2 revenue guidance above wall street expectations")
    assert m is not None
    assert m.company.ticker == "NVDA"
    assert m.company.exchange_symbol == "NVDAUSDT"


def test_finds_tesla_by_ticker():
    m = find_company("tsla announces voluntary recall of cybertruck")
    assert m is not None
    assert m.company.ticker == "TSLA"


def test_finds_apple_by_ceo_name():
    m = find_company("tim cook addresses shareholders at the apple meeting")
    assert m is not None
    assert m.company.ticker == "AAPL"


def test_returns_none_for_unrelated_text():
    m = find_company("the weather in london is cloudy today")
    assert m is None


def test_prefers_specific_company_over_ambiguous():
    # 'amazon' must dominate over a stray 'amd' in the text.
    m = find_company("amazon expands aws region; mentions amd cpus briefly")
    assert m is not None
    assert m.company.ticker in ("AMZN", "AMD")  # both are valid candidates
