from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.config import Settings
from app.schemas import CandidateBet
from app.services.coverage_contract import evaluate_publish_candidate, odds_sources_for_candidate
from app.services.core_provider_inventory_bridge import _counts, _normalize_row
from app.services.runner import PredictionRunner

UTC = timezone.utc


def candidate(**overrides) -> CandidateBet:
    base = dict(
        match_key="soccer|home|away|2026-05-16",
        sport_key="soccer",
        league_name="Premier League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 5, 16, 16, 0, tzinfo=UTC),
        family="totals",
        selection="Over",
        selection_key="over_2_5",
        odds=1.92,
        fair_odds=1.82,
        implied_probability=0.52,
        market_probability=0.52,
        consensus_probability=0.52,
        model_probability=0.57,
        final_probability=0.55,
        adjusted_probability=0.55,
        edge_pct=3.0,
        ev_pct=5.6,
        confidence=70.0,
        books_count=2,
        sources_count=2,
        stake_amount=10.0,
        source_summary={
            "sources": ["odds_api_io", "bzzoiro"],
            "books": ["Bet365", "Unibet"],
            "context_sources": ["sstats", "football_data"],
            "context_source": "ensemble",
        },
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.91},
            {"source": "bzzoiro", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.92},
        ],
    )
    base.update(overrides)
    return CandidateBet(**base)


def test_publish_contract_rejects_context_sources_as_price_confirmation(monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    item = candidate(
        sources_count=2,
        source_summary={
            "sources": ["newsapi", "weatherapi"],
            "books": ["News", "Weather"],
            "context_sources": ["newsapi", "weatherapi"],
            "context_source": "ensemble",
        },
        raw_bucket_offers=[
            {"source": "newsapi", "bookmaker": "News", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.91},
            {"source": "weatherapi", "bookmaker": "Weather", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.92},
        ],
    )

    decision = evaluate_publish_candidate(item, Settings(_env_file=None))

    assert not decision.passed
    assert decision.report["odds_sources_count"] == 0
    assert "insufficient_odds_sources:0/2" in decision.reasons


def test_publish_contract_accepts_two_price_and_two_context_sources(monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    item = candidate()

    decision = evaluate_publish_candidate(item, Settings(_env_file=None))

    assert decision.passed, decision.reasons
    assert decision.report["odds_sources"] == ["bzzoiro", "odds_api_io"]
    assert decision.report["context_sources"] == ["football_data", "sstats"]


def test_runner_publish_filter_blocks_single_source_candidate(monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    state_root = f".data/test-coverage-contract/{uuid4().hex}"
    monkeypatch.setenv("LINE_MOVEMENT_STATE_PATH", f"{state_root}/line_movement_state.json")
    monkeypatch.setenv("CANDIDATE_LIFECYCLE_STATE_PATH", f"{state_root}/candidate_lifecycle_state.json")
    settings = Settings(_env_file=None, PUBLISH_DRY_RUN=True)
    runner = PredictionRunner(settings)
    item = candidate(
        books_count=1,
        sources_count=1,
        source_summary={
            "sources": ["odds_api_io"],
            "books": ["Bet365"],
            "context_sources": ["sstats"],
            "context_source": "sstats",
        },
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.91},
        ],
    )

    publishable = runner._filter_publishable_candidates([item])

    assert publishable == []
    assert item.source_summary["publish_coverage_passed"] is False
    assert "insufficient_odds_sources:1/2" in item.source_summary["publish_coverage_reasons"]
    assert "insufficient_context_sources:1/2" in item.source_summary["publish_coverage_reasons"]
    assert "insufficient_books:1/2" in item.source_summary["publish_coverage_reasons"]


def test_odds_api_accounts_are_one_source_by_default(monkeypatch):
    monkeypatch.delenv("STRICT_PRICE_COUNT_API_ACCOUNTS_AS_SOURCES", raising=False)
    item = candidate(
        raw_bucket_offers=[
            {"source": "odds_api_io_account1", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.91},
            {"source": "odds_api_io_account2", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.92},
        ],
    )

    assert odds_sources_for_candidate(item) == {"odds_api_io"}


def test_inventory_keeps_books_and_independent_odds_sources_separate(monkeypatch):
    monkeypatch.setenv("PUBLISH_MIN_ODDS_SOURCES", "2")
    monkeypatch.setenv("PUBLISH_MIN_CONTEXT_SOURCES", "2")
    row = {
        "match_key": "soccer|home|away|2026-05-16",
        "odds_sources": ["odds_api_io"],
        "line_sources": ["odds_api_io"],
        "books": ["Bet365", "Unibet"],
        "price_confirmations": ["book:Bet365", "book:Unibet"],
        "context_sources": ["sstats", "bzzoiro"],
        "context_confirmations": ["provider:sstats", "provider:bzzoiro"],
        "metadata": {},
        "coverage": {},
    }

    _normalize_row(row, "2026-05-16T00:00:00+00:00")
    counts = _counts([row], {}, "2026-05-16T00:00:00+00:00")

    assert counts["matches_with_2plus_price_confirmations"] == 1
    assert counts["matches_with_2plus_odds_sources"] == 0
    assert counts["matches_ready_for_publish"] == 0


def test_publish_contract_reports_line_sources_separately_for_b_tier(monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    item = candidate(
        sources_count=1,
        books_count=2,
        source_summary={
            "sources": ["odds_api_io"],
            "books": ["Bet365", "Unibet"],
            "context_sources": ["sstats"],
        },
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.91},
            {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 2.5, "price": 1.92},
        ],
    )

    decision = evaluate_publish_candidate(item, Settings(_env_file=None))

    assert not decision.passed
    assert decision.report["odds_sources_count"] == 1
    assert decision.report["line_sources_count"] >= 2
    assert decision.report["context_sources_count"] == 1
