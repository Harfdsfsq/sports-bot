from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.providers.allsportsapi import AllSportsApiOddsProvider
from app.providers.football_data import FootballDataContextProvider
from app.providers.gnews import GNewsContextProvider
from app.providers.oddspapi import OddsPapiProvider
from app.schemas import CandidateBet, Match
from app.services.quality import PredictionQualityService
from app.services.telegram import TelegramPublisher
from app.utils import normalize_probability_percent, to_decimal_probability
from scripts.apply_provider_request_budget import build_env_for_decision, decide_provider

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


def test_request_budget_disables_monthly_provider_on_manual_run(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    now = datetime(2026, 4, 27, 6, 0, tzinfo=UTC)
    cfg = {
        "enabled": True,
        "manual_enabled": False,
        "per_run_max": 1,
        "safe_monthly_budget": 10,
        "disable_env": {"FUTRIXMETRICS_ENABLED": "false"},
    }

    decision = decide_provider("futrixmetrics", cfg, {"daily": {}, "monthly": {}}, now, "")
    env = build_env_for_decision(cfg, decision)

    assert decision["grant"] == 0
    assert decision["reason"] == "manual_disabled_by_policy"
    assert env["FUTRIXMETRICS_ENABLED"] == "false"
    assert env["FUTRIXMETRICS_MAX_HTTP_REQUESTS_PER_RUN"] == "0"


def test_request_budget_grants_allowed_scheduled_hour(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    now = datetime(2026, 4, 27, 6, 0, tzinfo=UTC)  # 09:00 MSK
    cfg = {
        "enabled": True,
        "allowed_msk_hours": [9, 15, 21],
        "per_run_max": 1,
        "safe_daily_budget": 18,
        "safe_monthly_budget": 720,
        "env": {"FUTRIXMETRICS_ENABLED": "true"},
    }
    state_row = {"daily": {}, "monthly": {}}

    decision = decide_provider("futrixmetrics", cfg, state_row, now, "")

    assert decision["grant"] == 1
    assert decision["reason"] == "granted"
    assert state_row["last_grant"] == 1


def test_request_budget_blocks_exhausted_daily_budget(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    now = datetime(2026, 4, 27, 6, 0, tzinfo=UTC)
    cfg = {"enabled": True, "per_run_max": 1, "safe_daily_budget": 1}
    state_row = {"daily": {"2026-04-27": 1}, "monthly": {}}

    decision = decide_provider("newsapi", cfg, state_row, now, "")

    assert decision["grant"] == 0
    assert decision["reason"] == "daily_budget_exhausted:1/1"


def test_gnews_zero_request_budget_short_circuits():
    provider = GNewsContextProvider(Settings(_env_file=None, ENABLE_GNEWS_CONTEXT=True, GNEWS_KEY="fake", GNEWS_PER_RUN_MAX=0))
    match = Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Premier League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        home_team_norm="",
        away_team_norm="",
        league_key="",
    )

    contexts, stats, _ = asyncio.run(provider.fetch_context([match]))

    assert contexts == {}
    assert stats["requests"] == 0
    assert stats["budget_exhausted"] is True


def test_football_data_zero_request_budget_short_circuits():
    provider = FootballDataContextProvider(
        Settings(
            _env_file=None,
            ENABLE_FOOTBALL_DATA_CONTEXT=True,
            FOOTBALL_DATA_API_KEY="fake",
            FOOTBALL_DATA_REQUESTS_MAX_PER_RUN=0,
        )
    )
    match = Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Premier League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        home_team_norm="",
        away_team_norm="",
        league_key="",
    )

    contexts, stats, _ = asyncio.run(provider.fetch_context([match]))

    assert contexts == {}
    assert stats["requests"] == 0
    assert stats["budget_exhausted"] is True


def test_telegram_uses_live_edge_values_and_keeps_structured_sections():
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
        adjusted_probability=0.50,
        edge_pct=3.5,
        ev_pct=7.5,
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

    assert "• Линия и value: Модель даёт 50.0% против 46.5% по линии" in message
    assert "• Линия и value: Линия недооценивает хозяев." not in message
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
        "rejections": {"publish_books_guard": 3},
        "filtering": {"publish_window_hours": 12, "min_kickoff_lead_minutes": 30},
    }

    message = publisher.render_run_report(summary)

    assert message is not None
    assert "Отчёт по запуску бота" in message
    assert "опубликовано прогнозов: 1" in message
    assert "Что ещё отсеялось:" in message
    assert "Почему нет прогноза:" not in message


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
    assert "Почему нет прогноза:" in message
    assert "publish books guard" in message
