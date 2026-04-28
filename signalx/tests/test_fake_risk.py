"""Anti-fake scoring tests."""
from app.analysis.fake_risk import AuthorMeta, compute_fake_risk


def test_official_press_link_clean_score_low():
    score = compute_fake_risk(
        text="Reuters: Nvidia raises Q2 revenue guidance above expectations.",
        author=AuthorMeta(
            handle="Reuters",
            verified=True,
            followers=25_000_000,
            account_age_days=4_000,
            has_authoritative_link=True,
            is_known_press=True,
        ),
        confirmation_count=1,
    )
    assert score < 30, f"expected clean fake_risk, got {score}"


def test_unverified_anon_with_breaking_keyword_high():
    score = compute_fake_risk(
        text="BREAKING: insider tip says Nvidia will buy AMD in 24 hours, 100x easy gains.",
        author=AuthorMeta(
            handle="random_anon",
            verified=False,
            followers=100,
            account_age_days=10,
            has_authoritative_link=False,
        ),
        confirmation_count=1,
    )
    assert score >= 70, f"expected high fake_risk, got {score}"


def test_cross_source_confirmation_lowers_score():
    base = dict(
        text="Tesla unveils new Cybertruck variant.",
        author=AuthorMeta(handle="rumourmill", verified=False, followers=2_000, account_age_days=400),
    )
    s_alone = compute_fake_risk(**base, confirmation_count=1)
    s_confirmed = compute_fake_risk(**base, confirmation_count=3)
    assert s_confirmed < s_alone, f"{s_confirmed=} should be lower than {s_alone=}"


def test_pump_pre_news_price_action_increases_score():
    base = dict(
        text="Apple announces iPhone refresh.",
        author=AuthorMeta(handle="presscorp", verified=True, followers=500_000, is_known_press=True),
        confirmation_count=1,
    )
    quiet = compute_fake_risk(**base, pre_tweet_price_change_pct=0.1)
    pumped = compute_fake_risk(**base, pre_tweet_price_change_pct=2.5)
    assert pumped > quiet


def test_score_clamps_to_0_100():
    very_bad = compute_fake_risk(
        text="BREAKING leak insider 100x guaranteed pump moon",
        author=AuthorMeta(verified=False, followers=10, account_age_days=2),
        confirmation_count=1,
        pre_tweet_price_change_pct=5.0,
    )
    very_good = compute_fake_risk(
        text="Apple files routine 8-K with the SEC.",
        author=AuthorMeta(
            is_known_official=True, verified=True, followers=50_000_000, account_age_days=5_000,
            has_authoritative_link=True,
        ),
        confirmation_count=5,
    )
    assert 0 <= very_bad <= 100
    assert 0 <= very_good <= 100
