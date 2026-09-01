from __future__ import annotations

from app.services.focused_alpha_evidence_truth import (
    evidence_truth,
    repair_candidate_evidence,
)
from scripts import build_focused_alpha_decisions as decisions
from scripts import promote_a_cover_value_candidates as promotion


def _history() -> dict:
    return {"live_learning_ready": False, "by_league": {}}


def test_exact_offer_sources_are_not_inflated_by_bookmakers_or_counters() -> None:
    candidate = {
        "odds_sources_count": 5,
        "books_count": 227,
        "source_summary": {
            "books": ["Bet365", "Unibet", "Betfair Exchange"],
            "raw_bucket_offers": [
                {"source": "odds_api_io", "bookmaker": "Bet365"},
                {"source": "odds_api_io", "bookmaker": "Unibet"},
                {"source": "odds_api_io", "bookmaker": "Betfair Exchange"},
            ],
        },
    }

    truth = evidence_truth(candidate)

    assert truth["odds_sources"] == ["odds_api_io"]
    assert truth["odds_sources_count"] == 1
    assert truth["books_count"] == 3


def test_context_aliases_collapse_and_synthetic_context_is_excluded() -> None:
    row = {
        "confirmation_sources": [
            "sstats",
            "sstats_form",
            "bzzoiro",
            "inventory_context",
            "runtime_context",
            "day inventory",
            "unknown",
        ]
    }

    truth = evidence_truth(row)

    assert truth["context_sources"] == ["bzzoiro", "sstats"]
    assert truth["context_sources_count"] == 2


def test_market_implied_xg_stays_non_hard_after_repair() -> None:
    candidate = {
        "match_key": "2026-07-24|a|b",
        "home_team": "A",
        "away_team": "B",
        "league_name": "League",
        "family": "totals",
        "selection": "Больше 2.5",
        "selection_key": "over",
        "point": 2.5,
        "odds": 2.10,
        "adjusted_probability": 0.58,
        "confidence": 82.0,
        "quality_score": 80.0,
        "quality_score_source": "raw",
        "odds_sources": ["odds_api_io", "bzzoiro"],
        "confirmation_sources": ["sstats", "clubelo"],
        "source_summary": {
            "books": ["Bet365", "Unibet"],
            "raw_bucket_offers": [
                {"source": "odds_api_io", "bookmaker": "Bet365"},
                {"source": "bzzoiro", "bookmaker": "Unibet"},
            ],
        },
        "expected_home": 1.6,
        "expected_away": 1.4,
        "diagnostics": {
            "xg_enrichment": {
                "source": "market_implied_total_xg",
                "source_mode": "market_implied_total_xg",
                "context_path": "market_probability_from_candidate",
            }
        },
        "line_movement_guard": {"status": "movement_confirmed", "passed": True},
    }

    repaired = repair_candidate_evidence(candidate)
    scored = decisions.score_candidate(repaired, _history())

    assert repaired["xg_source"] == "market_implied_or_proxy"
    assert scored["hard_xg"] is False
    assert "hard_xg_missing" in scored["blockers"]


def test_promotion_flag_cannot_override_missing_strict_evidence() -> None:
    inventory_row = {
        "tier_a_coverage_ready": True,
        "odds_sources_count": 5,
        "books_count": 227,
        "context_sources_count": 6,
        "metadata": {
            "verified_odds_sources": ["odds_api_io"],
            "verified_context_sources": ["sstats", "sstats_form", "inventory_context"],
        },
        "bookmakers": ["Bet365", "Unibet"],
    }

    assert promotion._is_a_cover(inventory_row) is False


def test_two_exact_providers_and_two_real_contexts_remain_a_cover() -> None:
    inventory_row = {
        "metadata": {
            "verified_odds_sources": ["odds_api_io", "bzzoiro"],
            "verified_context_sources": ["sstats", "clubelo"],
        },
        "bookmakers": ["Bet365", "Unibet"],
    }
    candidate = {
        "source_summary": {
            "books": ["Bet365", "Unibet"],
            "raw_bucket_offers": [
                {"source": "odds_api_io", "bookmaker": "Bet365"},
                {"source": "bzzoiro", "bookmaker": "Unibet"},
            ],
        }
    }

    tuned = promotion._tune_candidate(candidate, inventory_row)
    truth = evidence_truth(tuned, inventory_row=inventory_row)

    assert promotion._is_a_cover(inventory_row) is True
    assert truth["a_cover"] is True
    assert tuned["odds_sources_count"] == 2
    assert tuned["books_count"] == 2
    assert tuned["context_sources_count"] == 2
