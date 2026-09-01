from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app.services.autonomous_accumulation_runtime as runtime


@dataclass
class DummyMatch:
    home_team: str = "Home"
    away_team: str = "Away"
    league_name: str = "League"
    commence_time: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=2))

    @property
    def match_key(self) -> str:
        return "soccer|home|away|test"


@dataclass
class DummyOffer:
    source: str
    bookmaker: str
    family: str = "totals"
    selection: str = "Over"
    price: float = 2.0
    point: float = 2.5
    team_side: str | None = None


@dataclass
class DummyContext:
    source: str
    expected_home: float = 1.4
    expected_away: float = 1.2
    details: dict = field(default_factory=dict)


def test_coverage_requires_two_exact_odds_sources_and_two_core_contexts() -> None:
    match = DummyMatch()
    offers = {
        match.match_key: [
            DummyOffer("odds_api_io_account1", "Bet365"),
            DummyOffer("bzzoiro_v2", "Pinnacle"),
        ]
    }
    contexts = {
        match.match_key: {
            "bzzoiro": DummyContext("bzzoiro"),
            "sstats": DummyContext("sstats"),
            "weather": DummyContext("weather"),
        }
    }

    report = runtime._coverage_report([match], offers, contexts, datetime.now(UTC), "test-run")
    row = report["matches"][0]

    assert row["max_exact_odds_sources"] == 2
    assert row["max_exact_books"] == 2
    assert row["core_context_sources"] == ["bzzoiro", "sstats"]
    assert row["coverage_level"] == "L3"
    assert row["full_2plus_coverage"] is True


def test_weather_and_news_do_not_satisfy_core_context_contract() -> None:
    match = DummyMatch()
    offers = {
        match.match_key: [
            DummyOffer("odds_api_io", "Bet365"),
            DummyOffer("sportlogic", "Unibet"),
        ]
    }
    contexts = {
        match.match_key: {
            "weather": DummyContext("weather"),
            "newsapi": DummyContext("newsapi"),
        }
    }

    report = runtime._coverage_report([match], offers, contexts, datetime.now(UTC), "test-run-weather")
    row = report["matches"][0]

    assert row["all_context_source_count"] == 2
    assert row["core_context_source_count"] == 0
    assert row["coverage_level"] == "L0"
    assert "second_core_context" in row["missing_roles"]


def test_xg_probability_is_shrunk_toward_market(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "_candidate_coverage",
        lambda candidate, settings: {
            "odds_sources_count": 2,
            "books_count": 2,
            "core_context_sources_count": 2,
        },
    )
    candidate = SimpleNamespace(
        model_mode="xg_total",
        market_probability=0.50,
        model_probability=0.62,
        adjusted_probability=0.62,
        selected_odds=2.10,
        odds=2.10,
        selected_implied_probability=1 / 2.10,
        implied_probability=1 / 2.10,
        diagnostics={},
        source_summary={},
        edge_pct=0.0,
        ev_pct=0.0,
    )

    runtime._calibrate(candidate, object())
    report = candidate.diagnostics["autonomous_probability_calibration"]

    assert report["applied"] is True
    assert 0.50 < candidate.adjusted_probability < 0.62
    assert candidate.ev_pct == round((candidate.adjusted_probability * 2.10 - 1) * 100, 4)


def test_market_derived_candidate_is_shadow_only(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "_candidate_coverage",
        lambda candidate, settings: {
            "odds_sources_count": 3,
            "books_count": 3,
            "core_context_sources_count": 3,
        },
    )
    candidate = SimpleNamespace(
        model_mode="market_derived_totals",
        family="totals",
        expected_home=None,
        expected_away=None,
        model_probability=0.58,
        market_probability=0.52,
        ev_pct=4.0,
        confidence=70.0,
        source_summary={},
        diagnostics={},
        analysis={},
    )

    reasons = runtime._safety(candidate, object())

    assert any(reason.startswith("shadow_only_model_mode") for reason in reasons)


def test_rules_b_tier_accepts_one_odds_and_one_core_context(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "_candidate_coverage",
        lambda candidate, settings: {
            "odds_sources_count": 1,
            "books_count": 2,
            "core_context_sources_count": 1,
        },
    )
    settings = SimpleNamespace(
        min_sources_publish=1,
        min_books_publish=2,
        min_context_sources_publish=1,
    )
    candidate = SimpleNamespace(
        model_mode="xg_total",
        family="totals",
        expected_home=1.55,
        expected_away=1.05,
        model_probability=0.58,
        market_probability=0.52,
        ev_pct=4.0,
        confidence=70.0,
        source_summary={},
        diagnostics={},
        analysis={},
    )

    reasons = runtime._safety(candidate, settings)

    assert not any(reason.startswith("strict_odds_sources_below") for reason in reasons)
    assert not any(reason.startswith("strict_bookmakers_below") for reason in reasons)
    assert not any(reason.startswith("strict_core_context_sources_below") for reason in reasons)
