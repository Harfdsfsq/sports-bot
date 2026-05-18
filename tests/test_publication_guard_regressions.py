from __future__ import annotations

from app.services.coverage_contract import evaluate_publish_candidate, sync_candidate_publish_coverage
from app.services.market_family_publication_guard import _odds_source_count
from app.services.publication_lifecycle import is_sent_pick_row


def candidate_with_stale_summary() -> dict:
    return {
        "family": "totals",
        "selection": "over",
        "sources_count": 1,
        "books_count": 2,
        "source_summary": {
            "odds_sources_count": 1,  # stale value from an earlier stage
            "context_sources": ["sstats", "bzzoiro"],
        },
        "raw_bucket_offers": [
            {"source": "odds_api_io", "bookmaker": "bet365", "family": "totals", "point": 2.5},
            {"source": "bzzoiro", "bookmaker": "unibet", "family": "totals", "point": 2.5},
        ],
    }


def test_publish_coverage_uses_richest_odds_source_count_not_stale_summary() -> None:
    candidate = candidate_with_stale_summary()
    decision = sync_candidate_publish_coverage(candidate)

    assert decision.report["odds_sources_count"] == 2
    assert decision.report["context_sources_count"] == 2
    assert decision.passed is True
    assert candidate["source_summary"]["odds_sources_count"] == 2


def test_market_family_guard_reads_synced_coverage_contract() -> None:
    count, basis = _odds_source_count(candidate_with_stale_summary())

    assert count == 2
    assert "raw_bucket_offers" in basis or "normalized_odds_source_lists" in basis


def test_generated_latest_pick_is_not_treated_as_published() -> None:
    assert not is_sent_pick_row({"status": "generated", "telegram_sent": False})
    assert not is_sent_pick_row({"publication_lifecycle_status": "generated_not_sent"})
    assert is_sent_pick_row({"status": "pending", "telegram_sent": True})
    assert is_sent_pick_row({"publication_lifecycle_status": "telegram_sent"})
