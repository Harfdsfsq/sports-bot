from __future__ import annotations

from datetime import UTC, datetime

from app.providers.api_football import ApiFootballContextProvider
from app.schemas import Match
from app.services import strict_coverage_preparation_hook as preparation


def _match() -> Match:
    return Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Test League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
        home_team_norm="home",
        away_team_norm="away",
        league_key="test",
    )


def test_api_football_is_not_enabled_without_secret(monkeypatch) -> None:
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)

    activated = preparation._set_full_cohort_runtime()

    assert activated == {"api_football": False}


def test_api_football_is_enabled_with_bounded_budget(monkeypatch) -> None:
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-secret")

    activated = preparation._set_full_cohort_runtime()

    assert activated == {"api_football": True}
    assert preparation.os.environ["API_FOOTBALL_ENABLED"] == "true"
    assert preparation.os.environ["API_FOOTBALL_PER_RUN_MAX"] == "7"
    assert preparation.os.environ["API_FOOTBALL_CONTEXT_MATCH_LIMIT"] == "300"


def test_prediction_payload_builds_hard_context() -> None:
    prediction = {
        "predictions": {
            "winner": {"name": "Home FC", "comment": "Win or draw"},
            "goals": {"home": "1.8", "away": "0.9"},
            "percent": {"home": "55%", "draw": "27%", "away": "18%"},
            "advice": "Home team has the stronger recent form",
            "under_over": "+1.5",
        },
        "comparison": {"form": {"home": "62%", "away": "38%"}},
        "teams": {"home": {"last_5": {}}, "away": {"last_5": {}}},
        "league": {"form": "WWDLW"},
    }
    mapped = {"fixture_id": "42", "score": 91.0, "quality": "exact"}

    context = ApiFootballContextProvider._build_context(_match(), prediction, mapped)

    assert context is not None
    assert context.source == "api_football"
    assert context.expected_home == 1.8
    assert context.expected_away == 0.9
    assert context.home_win_probability == 0.55
    assert context.away_win_probability == 0.18
    assert context.details["hard_context"] is True
    assert context.details["fixture_only"] is False
    assert context.confidence >= 70.0


def test_fixture_only_payload_is_not_counted_as_context() -> None:
    context = ApiFootballContextProvider._build_context(
        _match(),
        {"predictions": {}, "comparison": {}, "teams": {}},
        {"fixture_id": "42", "score": 91.0, "quality": "exact"},
    )

    assert context is None
