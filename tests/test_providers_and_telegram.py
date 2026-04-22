from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.providers.allsportsapi import AllSportsApiOddsProvider
from app.providers.oddspapi import OddsPapiProvider
from app.schemas import CandidateBet, Match
from app.services.quality import PredictionQualityService
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


def test_run_report_renders_even_if_predictions_were_already_sent_when_allowed_by_settings():
    publisher = TelegramPublisher(Settings(_env_file=None, RUN_REPORT_ONLY_WHEN_NO_PREDICTIONS=False))
    summary = {
        "published_to_telegram": 1,
        "telegram_messages_sent": 1,
        "current_time_local": "2026-04-22T13:58:15+03:00",
        "matches_seen": 10,
        "matches_with_offers": 5,
        "contexts_built": 3,
        "candidates_before_quality": 2,
        "candidates_raw": 1,
        "candidates_publishable": 1,
        "rejections": {},
        "filtering": {"publish_window_hours": 12, "min_kickoff_lead_minutes": 30},
    }

    message = publisher.render_run_report(summary)

    assert message is not None
    assert "Отчёт по запуску бота" in message
    assert "опубликовано прогнозов: 1" in message


def test_run_report_renders_when_it_is_the_only_message_for_empty_run():
    publisher = TelegramPublisher(Settings(_env_file=None))
    summary = {
        "published_to_telegram": 0,
        "telegram_messages_sent": 0,
        "current_time_local": "2026-04-22T13:58:15+03:00",
        "matches_seen": 10,
        "matches_with_offers": 5,
        "contexts_built": 3,
        "candidates_before_quality": 1,
        "candidates_raw": 0,
        "candidates_publishable": 0,
        "rejections": {"publish_books_guard": 3},
        "filtering": {"publish_window_hours": 12, "min_kickoff_lead_minutes": 30},
    }

    message = publisher.render_run_report(summary)

    assert message is not None
    assert "Отчёт по запуску бота" in message
    assert "publish books guard" in message
