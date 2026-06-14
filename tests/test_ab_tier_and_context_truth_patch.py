
from scripts import build_day_inventory_coverage_truth as truth


def test_context_truth_merges_context_source_index():
    row = {
        "match_key": "soccer|a|b|2026-05-29",
        "coverage": {"odds": True, "context": True},
        "odds_sources": ["odds_api_io"],
        "books_count": 2,
        "context_sources": ["sstats"],
    }
    out = truth.row_truth(row, 2, 2, {"soccer|a|b|2026-05-29": ["clubelo"]})
    assert out["context_sources_count"] == 2
    assert out["tier_b_coverage_ready"] is False
    assert out["need_odds_sources"] == 1


def test_context_truth_keeps_single_context_when_no_index():
    row = {
        "match_key": "soccer|a|b|2026-05-29",
        "coverage": {"odds": True, "context": True},
        "odds_sources": ["odds_api_io"],
        "books_count": 2,
        "context_sources": ["sstats"],
    }
    out = truth.row_truth(row, 2, 2, {})
    assert out["context_sources_count"] == 1
    assert out["tier_a_coverage_ready"] is False


def test_tier_b_truth_requires_independent_odds_sources(monkeypatch):
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_BOOKS", "2")
    row = {
        "match_key": "soccer|a|b|2026-05-29",
        "coverage": {"odds": True, "context": True},
        "odds_sources": ["odds_api_io"],
        "books_count": 2,
        "context_sources": ["sstats"],
    }

    out = truth.row_truth(row, 2, 2, {})

    assert out["tier_b_coverage_ready"] is False
    assert out["tier_b_bookmaker_quorum_ready"] is False
    assert out["tier_b_confirmation_mode"] == "none"


def test_tier_b_truth_blocks_one_book_with_one_odds_source(monkeypatch):
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_BOOKS", "2")
    row = {
        "match_key": "soccer|a|b|2026-05-29",
        "coverage": {"odds": True, "context": True},
        "odds_sources": ["odds_api_io"],
        "books_count": 1,
        "context_sources": ["sstats"],
    }

    out = truth.row_truth(row, 2, 2, {})

    assert out["tier_b_coverage_ready"] is False
    assert out["tier_b_bookmaker_quorum_ready"] is False
