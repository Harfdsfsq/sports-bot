from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.providers.sportlogic_provider import SportLogicProvider
from app.schemas import Match


class DummySettings:
    sportlogic_api_key = "test-key"
    sportlogic_timeout_seconds = 2
    sportlogic_per_run_max = 6
    sportlogic_match_limit = 10
    sportlogic_odds_match_limit = 5
    run_days_ahead = 1
    match_start_tolerance_hours = 2
    fallback_match_start_tolerance_hours = 2


def _match() -> Match:
    return Match(
        source="odds_api_io",
        source_event_id="m1",
        sport_key="soccer",
        league_name="Portugal - Liga Portugal",
        home_team="Casa Pia Lisbon",
        away_team="SCU Torreense",
        commence_time=datetime(2026, 5, 28, 19, 0, tzinfo=timezone.utc),
        home_team_norm="",
        away_team_norm="",
        league_key="portugal",
    )


def test_sportlogic_flat_goals_over_under_uses_option_value_as_point() -> None:
    provider = SportLogicProvider(DummySettings())
    stats = provider._stats("offers")
    row = {
        "id": 6631453,
        "game_id": 41200,
        "market_id": 5,
        "market": {"id": 5, "key": "goals_over_under", "name": "Goals Over/Under"},
        "option_name": "Over",
        "option_value": "2.5",
        "odds": "1.95",
        "is_suspended": False,
        "bookmaker": {"name": "Bet365"},
    }

    offers = provider._parse_odds([row], _match(), "41200", stats)

    assert len(offers) == 1
    offer = offers[0]
    assert offer.source == "sportlogic"
    assert offer.bookmaker == "Bet365"
    assert offer.family == "totals"
    assert offer.selection == "Over"
    assert offer.point == 2.5
    assert offer.price == 1.95


def test_sportlogic_skips_suspended_flat_rows() -> None:
    provider = SportLogicProvider(DummySettings())
    stats = provider._stats("offers")
    row = {
        "game_id": 41200,
        "market": {"key": "goals_over_under"},
        "option_name": "Under",
        "option_value": "2.5",
        "odds": "1.90",
        "is_suspended": True,
        "bookmaker": {"name": "Bet365"},
    }

    offers = provider._parse_odds([row], _match(), "41200", stats)

    assert offers == []
    assert stats["parse_reject_reasons"]["suspended_odds_row"] == 1


def test_sportlogic_any_429_opens_runtime_cooldown(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPORTLOGIC_API_KEY", "test-key")
    monkeypatch.setenv("SPORTLOGIC_429_COOLDOWN_SECONDS", "120")
    provider = SportLogicProvider(DummySettings())
    response = httpx.Response(
        429,
        request=httpx.Request("GET", "https://api.sportlogic.io/api/v1/odds"),
        text="Too Many Requests",
    )

    provider._write_rate_limit_state(response)

    stats = provider._stats("matches")
    assert provider._ready(stats) is False
    assert stats["reason"] == "sportlogic_rate_limit_open"
    assert stats["rate_limited"] is True
