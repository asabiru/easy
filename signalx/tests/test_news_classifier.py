"""News classifier + sentiment tests."""
from app.analysis.classifier import classify
from app.analysis.impact_score import compute_impact
from app.analysis.sentiment import refine


def test_guidance_raised_is_bullish_high_impact():
    c = classify("nvidia raises guidance above wall street expectations")
    c = refine(c, "nvidia raises guidance above wall street expectations")
    assert c.event_type == "guidance_raised"
    assert c.direction == "bullish"
    assert compute_impact(c) >= 80


def test_lawsuit_lost_is_bearish():
    txt = "company loses lawsuit ordered to pay 1 billion"
    c = refine(classify(txt), txt)
    assert c.event_type == "lawsuit_lost"
    assert c.direction == "bearish"
    assert compute_impact(c) >= 75


def test_other_event_unclear_is_neutralised_by_sentiment():
    txt = "tesla unveils new lineup at gigafactory"
    c = refine(classify(txt), txt)
    assert c.direction in ("bullish", "neutral")


def test_irrelevant_is_other_unclear():
    c = classify("the local bakery announced a new bread")
    assert c.event_type == "other"
    assert c.direction == "unclear"
    assert compute_impact(c) == 0 or compute_impact(c) <= 10


def test_export_restriction_is_bearish_high_impact():
    txt = "us imposes export restriction on advanced chips to china"
    c = refine(classify(txt), txt)
    assert c.event_type == "export_restriction"
    assert c.direction == "bearish"
    assert compute_impact(c) >= 75
