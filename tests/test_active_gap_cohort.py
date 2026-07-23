from __future__ import annotations

from app.services.daily_coverage_assignments import build_assignments
from app.services.daily_coverage_fixed_cohort_patch import _coverage


def test_fixed_cohort_restores_only_verified_strict_sources() -> None:
    row = {
        "metadata": {
            "verified_odds_sources": ["odds_api_io", "bzzoiro"],
            "verified_context_sources": ["sstats", "thesportsdb"],
        },
        "coverage": {
            "daily_coverage_evidence_synced": True,
            "odds_sources": ["odds_api_io", "bzzoiro"],
            "context_sources": ["sstats", "thesportsdb"],
        },
    }

    odds, contexts = _coverage(row, {})

    assert set(odds) == {"odds_api_io", "bzzoiro"}
    assert set(contexts) == {"sstats", "thesportsdb"}


def test_fixed_cohort_does_not_promote_unsynced_proxy_lists() -> None:
    row = {
        "coverage": {
            "daily_coverage_evidence_synced": False,
            "odds_sources": ["proxy_odds"],
            "context_sources": ["fixture_alias", "proxy_context"],
        }
    }

    assert _coverage(row, {}) == ([], [])


def test_assignments_skip_expired_rows_and_target_only_active_gaps() -> None:
    rows = [
        {
            "match_key": "expired",
            "hours_to_kickoff": -8.0,
            "provider_assignment_eligible": False,
            "odds_sources": [],
            "context_sources": [],
            "league_name": "Premier League",
            "home_team": "Old Home",
            "away_team": "Old Away",
        },
        {
            "match_key": "covered",
            "hours_to_kickoff": 12.0,
            "provider_assignment_eligible": True,
            "odds_sources": ["odds_api_io", "bzzoiro"],
            "context_sources": ["sstats", "thesportsdb"],
            "league_name": "Premier League",
            "home_team": "Covered Home",
            "away_team": "Covered Away",
        },
        {
            "match_key": "gap",
            "hours_to_kickoff": 12.0,
            "provider_assignment_eligible": True,
            "odds_sources": ["odds_api_io"],
            "context_sources": ["sstats"],
            "league_name": "Premier League",
            "home_team": "Gap Home",
            "away_team": "Gap Away",
        },
    ]

    assignments = build_assignments(rows, run_index=1)

    for roles in assignments.values():
        for keys in roles.values():
            assert "expired" not in keys
            assert "covered" not in keys
    assert "gap" in assignments["bzzoiro"]["offers"]
    assert "gap" in assignments["bzzoiro"]["context"]
