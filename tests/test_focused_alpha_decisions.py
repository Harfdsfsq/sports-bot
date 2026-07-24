from __future__ import annotations

from scripts import build_focused_alpha_decisions as decisions


def _history() -> dict:
    return {"live_learning_ready": False, "by_league": {}}


def test_strong_candidate_passes_conservative_shadow_contract() -> None:
    candidate = {
        "match_key": "2026-07-24|a|b",
        "home_team": "A",
        "away_team": "B",
        "league_name": "League A",
        "family": "totals",
        "selection": "Меньше 2.5",
        "selection_key": "under",
        "point": 2.5,
        "odds": 2.10,
        "adjusted_probability": 0.58,
        "market_probability": 0.49,
        "confidence": 80.0,
        "quality_score": 82.0,
        "quality_score_source": "raw",
        "odds_sources": ["odds_api_io", "bzzoiro"],
        "confirmation_sources": ["sstats", "espn"],
        "books_count": 3,
        "expected_home": 1.05,
        "expected_away": 0.95,
        "xg_source": "sstats",
        "line_movement_guard": {"status": "movement_confirmed", "passed": True},
    }

    scored = decisions.score_candidate(candidate, _history())

    assert scored["conservative_probability"] < scored["model_probability"]
    assert scored["conservative_ev_pct"] > 2.0
    assert scored["passes_shadow_contract"] is True
    assert scored["blockers"] == []


def test_raw_ev_cannot_override_missing_hard_evidence() -> None:
    candidate = {
        "match_key": "2026-07-24|c|d",
        "home_team": "C",
        "away_team": "D",
        "league_name": "League B",
        "family": "totals",
        "selection": "Больше 2.5",
        "selection_key": "over",
        "point": 2.5,
        "odds": 2.40,
        "adjusted_probability": 0.60,
        "confidence": 82.0,
        "quality_score": 80.0,
        "quality_score_source": "proxy",
        "odds_sources_count": 1,
        "confirmation_sources_count": 1,
        "books_count": 1,
        "xg_source": "market_implied_proxy",
    }

    scored = decisions.score_candidate(candidate, _history())

    assert scored["ev_pct"] > 0
    assert scored["passes_shadow_contract"] is False
    assert "odds_sources_below_2" in scored["blockers"]
    assert "context_sources_below_2" in scored["blockers"]
    assert "bookmaker_quorum_below_2" in scored["blockers"]
    assert "hard_xg_missing" in scored["blockers"]
    assert "movement_not_confirmed" in scored["blockers"]
