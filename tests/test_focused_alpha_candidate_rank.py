from __future__ import annotations

from types import SimpleNamespace

from scripts import patch_focused_alpha_candidate_rank as patch


def test_rank_patch_keeps_legacy_rank_as_tie_break(monkeypatch) -> None:
    monkeypatch.setenv("FOCUSED_ALPHA_ENABLED", "true")
    monkeypatch.setattr(patch, "_INSTALLED", False)
    monkeypatch.setattr(patch, "_ORIGINAL", None)
    monkeypatch.setattr(patch, "_HISTORY", {"live_learning_ready": False, "by_league": {}})
    monkeypatch.setattr(patch, "_write", lambda payload: None)

    base = SimpleNamespace(candidate_rank=lambda candidate, metrics, tier: (10.0, 2.0, 70.0, 30.0, 75.0))
    result = patch.install(base)
    candidate = {
        "match_key": "2026-07-24|a|b",
        "home_team": "A",
        "away_team": "B",
        "league_name": "League",
        "family": "totals",
        "selection": "Меньше 2.5",
        "selection_key": "under",
        "point": 2.5,
        "expected_home": 1.0,
        "expected_away": 1.0,
        "xg_source": "sstats",
        "line_movement_guard": {"status": "movement_confirmed", "passed": True},
    }
    metrics = {
        "odds": 2.10,
        "adjusted_probability": 0.58,
        "model_probability": 0.58,
        "market_probability": 0.49,
        "canonical_edge_pp": 10.38,
        "canonical_ev_pct": 21.8,
        "confidence": 80.0,
        "quality_score": 82.0,
        "quality_score_source": "raw",
        "publication_score": 80.0,
        "books_count": 3,
        "odds_sources_count": 2,
        "confirmation_sources_count": 2,
        "confirmation_sources": ["sstats", "espn"],
        "xg_sanity": {"enabled": True, "xg_direction_ok": True},
        "btts_sanity": {},
        "dnb_sanity": {},
    }

    rank = base.candidate_rank(candidate, metrics, "уровень A")

    assert result["changes_publishability"] is False
    assert len(rank) == 7
    assert rank[-5:] == (10.0, 2.0, 70.0, 30.0, 75.0)
