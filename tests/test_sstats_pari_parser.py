from __future__ import annotations

from datetime import UTC, datetime

from app.providers.sstats_pari_parser import parse_market_name
from app.schemas import Match


def _match() -> Match:
    return Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        home_team_norm="home",
        away_team_norm="away",
        league_key="league",
    )


def test_russian_total_is_canonical() -> None:
    assert parse_market_name("Тотал больше (2.5)", _match()) == (
        "totals", "Over", 2.5, None, "totals:over:2.5"
    )


def test_russian_spread_is_canonical() -> None:
    assert parse_market_name("Фора 2 (+1.5)", _match()) == (
        "spreads", "Away FC", 1.5, "away", "spreads:away:1.5"
    )
