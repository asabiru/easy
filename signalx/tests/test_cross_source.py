"""Cross-source sliding window tests."""
import time

from app.news import cross_source


def setup_function(_):
    cross_source.reset_for_tests()
    cross_source._store = None  # force re-init


def test_first_source_returns_one():
    n = cross_source.record_observation("NVDA", "guidance_raised", "reuters")
    assert n == 1


def test_two_sources_yield_two():
    cross_source.record_observation("NVDA", "guidance_raised", "reuters")
    n = cross_source.record_observation("NVDA", "guidance_raised", "bloomberg")
    assert n == 2


def test_same_source_does_not_double_count():
    cross_source.record_observation("NVDA", "guidance_raised", "reuters")
    n = cross_source.record_observation("NVDA", "guidance_raised", "reuters")
    assert n == 1


def test_unrelated_event_independent():
    cross_source.record_observation("NVDA", "earnings_beat", "reuters")
    n = cross_source.record_observation("TSLA", "earnings_beat", "reuters")
    assert n == 1


def test_old_observations_evicted(monkeypatch):
    store = cross_source._InMemoryStore(window_sec=1)
    cross_source._store = store
    cross_source.record_observation("NVDA", "lawsuit", "reuters")
    time.sleep(1.1)
    n = cross_source.record_observation("NVDA", "lawsuit", "bloomberg")
    # The reuters entry should have been evicted, leaving only bloomberg in the window.
    assert n == 1
