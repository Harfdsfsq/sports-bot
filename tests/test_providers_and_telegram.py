from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.providers.allsportsapi import AllSportsApiOddsProvider
from app.providers.football_data import FootballDataContextProvider
from app.providers.gnews import GNewsContextProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.oddspapi import OddsPapiProvider
from app.schemas import CandidateBet, Match
from app.services.quality import PredictionQualityService
from app.services.telegram import TelegramPublisher
from app.utils import normalize_probability_percent, to_decimal_probability
from scripts.apply_provider_request_budget import build_env_for_decision, decide_provider, final_market_integrity_env, market_integrity_check
from scripts.publish_controlled_fallback import final_publish_guard_reasons, hard_reject_reasons, xg_sanity_metrics
from scripts.send_harizon_telegram_run_report_v5 import (
    provider_auth_failed,
    provider_plan_restricted,
)

UTC = timezone.utc


class _FakeOddsApiIoResponse:
    status_code = 401
    text = '{"error":"You need to provide a valid apiKey"}'


class _OddsResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload)
        )

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


def test_odds_api_io_stops_event_paging_on_auth_error(monkeypatch):
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            calls.append({"url": url, "params": dict(params or {})})
            return _FakeOddsApiIoResponse()

    monkeypatch.setattr("app.providers.odds_api_io.httpx.AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        odds_api_io_key="bad-key",
        odds_api_io_per_run_max=20,
        odds_api_io_max_pages_per_sport=10,
    )
    provider = OddsApiIoProvider(settings)

    matches, stats, _preview = asyncio.run(provider.fetch_matches())

    assert matches == []
    assert len(calls) == 1
    assert stats["event_requests"] == 1
    assert stats["response_errors"] == 1
    assert stats["auth_error"] is True
    assert stats["auth_status_code"] == 401
    assert stats["stop_reason"] == "auth_error"


def test_report_detects_odds_api_io_auth_failure_from_statuses():
    assert provider_auth_failed({"event_http_statuses": [401]}) is True
    assert provider_auth_failed({"last_body_preview": '{"error":"You need to provide a valid apiKey"}'}) is True
    assert provider_auth_failed({"event_http_statuses": [200], "last_body_preview": "ok"}) is False


def test_report_does_not_call_paid_plan_restriction_an_invalid_key():
    row = {
        "auth_error": True,
        "odds_http_statuses": [200, 403],
        "last_body_preview": (
            "Betfair Exchange is a sharp or exchange book and is only available "
            "on our paid plans"
        ),
    }

    assert provider_plan_restricted(row) is True
    assert provider_auth_failed(row) is False


def test_odds_api_io_keeps_valid_second_account_bookmakers(monkeypatch):
    monkeypatch.delenv("ODDS_API_IO_PAID_PLAN_ENABLED", raising=False)
    settings = Settings(
        _env_file=None,
        odds_api_io_key="key-1",
        odds_api_io_key_2="key-2",
        odds_api_io_bookmakers_account2=["Betfair Exchange", "Sbobet"],
    )

    accounts = OddsApiIoProvider(settings)._odds_accounts()

    assert accounts[1]["bookmakers"] == "Betfair Exchange,Sbobet"
    assert accounts[1]["fallback_bookmakers"] == "William Hill,Betway"


def test_odds_api_io_recovers_plan_restricted_account_with_recreational_books():
    calls: list[str] = []
    responses = [
        _OddsResponse(
            403,
            {
                "error": (
                    "Betfair Exchange is a sharp or exchange book, and those "
                    "are only available on our paid plans"
                )
            },
        ),
        _OddsResponse(200, [{"id": 101, "bookmakers": {}}]),
        _OddsResponse(200, [{"id": 102, "bookmakers": {}}]),
    ]

    class FakeClient:
        async def get(self, _url, params=None):
            calls.append(str((params or {}).get("bookmakers") or ""))
            return responses.pop(0)

    provider = OddsApiIoProvider(
        Settings(
            _env_file=None,
            odds_api_io_key_2="key-2",
            odds_api_io_per_run_max=10,
        )
    )
    stats = {
        "accounts": {},
        "odds_requests": 0,
        "response_errors": 0,
        "odds_http_statuses": [],
        "payload_shapes": [],
    }

    first = asyncio.run(
        provider._fetch_odds_multi_chunk(
            FakeClient(),
            "key-2",
            [101],
            "Betfair Exchange,Sbobet",
            stats,
            account_name="account2",
            fallback_books="William Hill,Betway",
        )
    )
    second = asyncio.run(
        provider._fetch_odds_multi_chunk(
            FakeClient(),
            "key-2",
            [102],
            "Betfair Exchange,Sbobet",
            stats,
            account_name="account2",
            fallback_books="William Hill,Betway",
        )
    )

    assert [row["id"] for row in first + second] == [101, 102]
    assert calls == [
        "Betfair Exchange,Sbobet",
        "William Hill,Betway",
        "William Hill,Betway",
    ]
    assert stats["response_errors"] == 0
    assert stats["plan_restriction_responses"] == 1
    assert stats["plan_restriction_recovered"] is True
    assert stats["accounts"]["account2"]["entitlement_fallback_used"] is True
    assert provider_plan_restricted(stats) is False
    assert provider_auth_failed(stats) is False


def test_odds_api_io_stops_restricted_account_after_fallback_is_rejected():
    calls: list[str] = []

    class FakeClient:
        async def get(self, _url, params=None):
            calls.append(str((params or {}).get("bookmakers") or ""))
            return _OddsResponse(
                403,
                {"error": "book is not included in your plan"},
            )

    provider = OddsApiIoProvider(
        Settings(
            _env_file=None,
            odds_api_io_key_2="key-2",
            odds_api_io_per_run_max=10,
        )
    )
    stats = {
        "accounts": {},
        "odds_requests": 0,
        "response_errors": 0,
        "odds_http_statuses": [],
        "payload_shapes": [],
    }
    args = (
        FakeClient(),
        "key-2",
        [101],
        "Betfair Exchange,Sbobet",
        stats,
    )

    first = asyncio.run(
        provider._fetch_odds_multi_chunk(
            *args,
            account_name="account2",
            fallback_books="William Hill,Betway",
        )
    )
    second = asyncio.run(
        provider._fetch_odds_multi_chunk(
            *args,
            account_name="account2",
            fallback_books="William Hill,Betway",
        )
    )

    assert first == []
    assert second == []
    assert calls == ["Betfair Exchange,Sbobet", "William Hill,Betway"]
    assert stats["accounts"]["account2"]["plan_restriction"] is True
    assert stats["response_errors"] == 1


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


def test_request_budget_final_env_keeps_strict_publish_contract(monkeypatch):
    monkeypatch.delenv("HARIZON_FAST_INVENTORY_LOCK", raising=False)
    monkeypatch.delenv("DAY_INVENTORY_FAST_MODE", raising=False)
    monkeypatch.setenv("DAY_INVENTORY_FORCE_PROVIDER_MERGE", "true")
    monkeypatch.setenv("PUBLISH_DRY_RUN", "false")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("MANUAL_CONTROLLED_PUBLISH_ENABLED", "false")

    env = final_market_integrity_env()
    check = market_integrity_check(env, "test")

    assert env["HARIZON_FAST_INVENTORY_LOCK"] == "false"
    assert env["DAY_INVENTORY_FORCE_PROVIDER_MERGE"] == "true"
    assert env["PUBLISH_DRY_RUN"] == "false"
    assert env["CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"] == "2"
    assert env["CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS"] == "2"
    assert env["CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM"] == "true"
    assert check["status"] == "ok"


def test_xg_guard_allows_conservative_total_model(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP", "14.0")
    candidate = {
        "family": "totals",
        "selection": "Under",
        "point": 2.5,
        "expected_home": 0.82,
        "expected_away": 0.88,
    }

    xg = xg_sanity_metrics(candidate, adjusted_probability=0.55)
    metrics = {
        "odds": 1.96,
        "canonical_edge_pp": 4.2,
        "canonical_ev_pct": 8.4,
        "adjusted_probability": 0.55,
        "books_count": 2,
        "sources_count": 1,
        "xg_sanity": xg,
    }

    assert xg["xg_model_optimism_gap_pp"] == 0.0
    assert "xg_probability_gap_hard_reject" not in hard_reject_reasons(candidate, metrics, {})


def test_xg_guard_rejects_only_model_optimism(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP", "14.0")
    candidate = {
        "family": "totals",
        "selection": "Under",
        "point": 2.5,
        "expected_home": 0.82,
        "expected_away": 0.88,
    }

    xg = xg_sanity_metrics(candidate, adjusted_probability=0.91)
    metrics = {
        "odds": 1.96,
        "canonical_edge_pp": 4.2,
        "canonical_ev_pct": 8.4,
        "adjusted_probability": 0.91,
        "books_count": 2,
        "sources_count": 1,
        "xg_sanity": xg,
    }

    assert xg["xg_model_optimism_gap_pp"] > 14.0
    assert "xg_probability_gap_hard_reject" in hard_reject_reasons(candidate, metrics, {})


def test_controlled_fallback_allows_tiny_final_edge_miss_with_clean_confirmation(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP", "3.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT", "6.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_PP", "0.15")
    candidate = {"family": "totals", "selection": "Under", "point": 2.5}
    metrics = {
        "canonical_edge_pp": 2.9,
        "canonical_ev_pct": 7.4,
        "quality_score": 80.3,
        "confidence": 81.1,
        "books_count": 4,
        "sources_count": 2,
        "confirmation_sources_count": 2,
        "quality_score_source": "raw",
        "xg_sanity": {"enabled": True, "xg_direction_ok": True, "xg_model_optimism_gap_pp": 0.0},
    }

    reasons = final_publish_guard_reasons(candidate, metrics, "B")

    assert "final_edge_below_min" not in reasons
    assert metrics["final_edge_tolerance_used"]["edge_pp"] == 2.9


def test_controlled_fallback_keeps_xg_conflict_hard_even_near_edge(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP", "3.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT", "6.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_PP", "0.15")
    candidate = {"family": "totals", "selection": "Under", "point": 2.5}
    metrics = {
        "canonical_edge_pp": 2.9,
        "canonical_ev_pct": 7.4,
        "quality_score": 80.3,
        "confidence": 81.1,
        "books_count": 4,
        "sources_count": 2,
        "confirmation_sources_count": 2,
        "quality_score_source": "raw",
        "xg_sanity": {"enabled": True, "xg_direction_ok": False, "xg_model_optimism_gap_pp": 0.0},
    }

    reasons = final_publish_guard_reasons(candidate, metrics, "B")

    assert "xg_direction_conflict" in reasons
    assert "final_edge_below_min" in reasons
    assert "final_edge_tolerance_used" not in metrics


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


def test_quality_historical_relief_accepts_multi_source_totals_near_edge(monkeypatch):
    monkeypatch.setenv("HISTORICAL_SEGMENT_RELIEF_ENABLED", "true")
    service = PredictionQualityService(Settings(_env_file=None))
    candidate = CandidateBet(
        match_key="soccer|sarpsborg 08|vaalerenga if|2026-05-16",
        sport_key="soccer",
        league_name="Norway Eliteserien",
        home_team="Vaalerenga IF",
        away_team="Sarpsborg 08",
        commence_time=datetime(2026, 5, 16, 18, 0, tzinfo=UTC),
        family="totals",
        selection="Under",
        selection_key="under",
        odds=2.56,
        fair_odds=2.43,
        implied_probability=0.391,
        market_probability=0.391,
        consensus_probability=0.378,
        model_probability=0.493,
        final_probability=0.493,
        adjusted_probability=0.421,
        edge_pct=2.9,
        ev_pct=7.4,
        confidence=81.0,
        books_count=4,
        sources_count=2,
        point=2.5,
        expected_home=1.32,
        expected_away=1.01,
        publication_score=70.0,
        source_summary={
            "context_source": "ensemble",
            "context_sources": ["sstats", "bzzoiro"],
            "context_sources_count": 2,
            "match_tier": "secondary",
        },
    )
    decisions = [{
        "match_key": candidate.match_key,
        "selection_key": candidate.selection_key,
        "status": "rejected_by_quality_filters",
        "reasons": ["bad_historical_segment_guard"],
    }]

    selected = service._select_historical_guard_relief_candidate([candidate], decisions)

    assert selected is candidate
    assert candidate.source_summary["historical_segment_relief"]["edge_tolerance_pp"] == 0.35


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
    publisher = TelegramPublisher(Settings(_env_file=None, RUN_REPORT_ENABLED=True, RUN_REPORT_ONLY_WHEN_NO_PREDICTIONS=False))
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
    publisher = TelegramPublisher(Settings(_env_file=None, RUN_REPORT_ENABLED=True))
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
