from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.providers.allsportsapi import AllSportsApiOddsProvider
from app.providers.oddspapi import OddsPapiProvider
from app.schemas import CandidateBet, Match
from app.services.telegram import TelegramPublisher
from app.utils import normalize_probability_percent, to_decimal_probability

UTC = timezone.utc


def test_probability_helpers_accept_percent_strings():
    assert normalize_probability_percent("10%") == 10.0
    assert normalize_probability_percent("0,42") == 42.0
    assert to_decimal_probability("10%") == 0.1
    assert to_decimal_probability("62,5") == 0.625


def test_allsportsapi_parses_extra_market_families():
    provider = AllSportsApiOddsProvider(Settings(_env_file=None))
    match = Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Test League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        home_team_norm="",
        away_team_norm="",
        league_key="",
    )
    rows = [
        {
            "odd_bookmakers": "Bet365",
            "odd_1": "1.80",
            "odd_x": "3.40",
            "odd_2": "4.20",
            "odd_1x": "1.22",
            "odd_x2": "1.95",
            "odd_12": "1.30",
            "bts_yes": "1.77",
            "bts_no": "2.05",
            "o+2.5": "1.90",
            "u+2.5": "1.90",
            "ah0_1": "1.70",
            "ah0_2": "2.10",
        }
    ]

    offers = provider._parse_odds_rows(rows, match, "event-1")
    families = {(offer.family, offer.selection, offer.point, offer.team_side) for offer in offers}

    assert ("doubleChance", "1X", None, None) in families
    assert ("btts", "Yes", None, None) in families
    assert ("dnb", "Home FC", 0.0, "home") in families
    assert ("dnb", "Away FC", 0.0, "away") in families
    assert ("totals", "Over", 2.5, None) in families


def test_oddspapi_splits_fixture_windows_under_limit():
    provider = OddsPapiProvider(Settings(_env_file=None))
    start = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    end = start + timedelta(days=4)

    windows = provider._fixture_windows(start, end)

    assert len(windows) >= 2
    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all((window_end - window_start) <= timedelta(hours=provider.fixture_window_hours) for window_start, window_end in windows)


def test_telegram_uses_structured_analysis_breakdown():
    publisher = TelegramPublisher(Settings(_env_file=None))
    bet = CandidateBet(
        match_key="soccer::home::away::2026-04-22T12:00:00+00:00",
        sport_key="soccer",
        league_name="Test League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 4, 22, 15, 0, tzinfo=UTC),
        family="h2h",
        selection="Home FC",
        selection_key="home",
        odds=2.15,
        fair_odds=1.95,
        implied_probability=0.465,
        market_probability=0.465,
        consensus_probability=0.465,
        model_probability=0.54,
        final_probability=0.54,
        adjusted_probability=0.54,
        edge_pct=7.5,
        ev_pct=16.1,
        confidence=64.0,
        books_count=2,
        sources_count=2,
        expected_home=1.62,
        expected_away=0.94,
        analysis={
            "sections": {
                "edge": "Линия недооценивает хозяев.",
                "xg": "По xG матч тянет к 1.62 : 0.94.",
                "form": "По форме хозяева выглядят лучше.",
                "table": "По таблице Home FC идёт выше.",
                "market": "Рынок подтверждает идею двумя букмекерами.",
            }
        },
        source_summary={"books": ["Bet365", "Unibet"], "sources": ["odds_api_io", "allsportsapi"]},
    )

    message = publisher.render_message([bet])

    assert "• Линия и value: Линия недооценивает хозяев." in message
    assert "• xG: По xG матч тянет к 1.62 : 0.94." in message
    assert "• Форма: По форме хозяева выглядят лучше." in message
    assert "• Таблица: По таблице Home FC идёт выше." in message
