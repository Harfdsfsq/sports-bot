from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.schemas import CandidateBet
from app.services.line_movement_state import evaluate_and_record_line_movement
from app.services.publication_tiers import classify_publication_tier

UTC = timezone.utc


@dataclass
class TierSettings:
    line_movement_next_run_minutes: int = 120
    min_sources_publish: int = 2
    min_context_sources_publish: int = 2
    min_books_publish: int = 2


def _candidate(commence_time: datetime) -> CandidateBet:
    return CandidateBet(
        match_key="soccer|home|away|2026-06-01",
        sport_key="soccer",
        league_name="Friendly",
        home_team="Home",
        away_team="Away",
        commence_time=commence_time,
        family="totals",
        selection="Больше 2.5",
        selection_key="over_2_5",
        odds=2.05,
        fair_odds=1.9,
        implied_probability=0.48,
        market_probability=0.48,
        consensus_probability=0.48,
        model_probability=0.56,
        final_probability=0.55,
        adjusted_probability=0.55,
        edge_pct=4.0,
        ev_pct=12.0,
        confidence=72.0,
        books_count=2,
        sources_count=2,
        source_summary={
            "sources": ["odds_api_io", "bzzoiro"],
            "books": ["Bet365", "Unibet"],
            "context_sources": ["sstats", "football_data"],
        },
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 2.04},
            {"source": "bzzoiro", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 2.5, "price": 2.05},
        ],
    )


def test_strict_candidate_can_publish_when_no_next_cron(monkeypatch):
    now = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    state_path = f".data/test-line-movement-no-next-cron/{uuid4().hex}.json"
    monkeypatch.setenv("LINE_MOVEMENT_STATE_PATH", state_path)
    monkeypatch.setenv("PUBLISH_MIN_ODDS_SOURCES", "2")
    monkeypatch.setenv("PUBLISH_MIN_CONTEXT_SOURCES", "2")

    decision = classify_publication_tier(_candidate(now + timedelta(minutes=80)), TierSettings(), now=now)

    assert decision.passed is True
    assert decision.tier == "A"
    assert decision.report["line_movement"]["status"] == "publish_now_no_next_cron"
    assert decision.report["tier_b_bookmaker_quorum_passed"] is True
    assert decision.report["tier_b_strict_coverage_passed"] is True
    assert decision.report["tier_b_confirmation_mode"] == "strict_independent_sources"


def test_b_tier_bookmaker_quorum_requires_two_books(monkeypatch):
    now = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    state_path = f".data/test-line-movement-one-book-blocked/{uuid4().hex}.json"
    monkeypatch.setenv("LINE_MOVEMENT_STATE_PATH", state_path)
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_BOOKS", "2")
    item = _candidate(now + timedelta(minutes=80))
    item.books_count = 1
    item.raw_bucket_offers = [
        {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 2.04},
    ]
    item.source_summary["books"] = ["Bet365"]
    item.source_summary["sources"] = ["odds_api_io"]

    decision = classify_publication_tier(item, TierSettings(), now=now)

    assert decision.passed is False
    assert decision.tier == "blocked"
    assert any("odds_sources=1/2" in reason and "bookmakers=1/2" in reason for reason in decision.reasons)


def test_first_snapshot_waits_when_scheduled_cron_exists_before_kickoff(monkeypatch):
    now = datetime(2026, 6, 1, 13, 51, tzinfo=UTC)  # 16:51 MSK
    kickoff = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)  # 19:00 MSK
    state_path = f".data/test-line-movement-waits-for-18msk/{uuid4().hex}.json"
    monkeypatch.setenv("LINE_MOVEMENT_STATE_PATH", state_path)
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("LINE_MOVEMENT_CRON_INTERVAL_MINUTES", "120")
    monkeypatch.setenv("LINE_MOVEMENT_MIN_LEAD_MINUTES", "15")

    decision = evaluate_and_record_line_movement(_candidate(kickoff), TierSettings(), now=now)

    assert decision["passed"] is False
    assert decision["status"] == "awaiting_next_run"
    assert decision["next_scheduled_run_at_utc"] == "2026-06-01T15:00:00+00:00"
    assert decision["has_next_regular_run_before_kickoff"] is True


def test_controlled_fallback_context_index_uses_current_evidence_exports(monkeypatch):
    import scripts.publish_controlled_fallback as fallback

    base = Path(".data/test-controlled-fallback-context-index")
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(base)
    export_dir = Path(".data") / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "latest-context-observations.json").write_text(
        json.dumps(
            [
                {
                    "match_key": "soccer|bulgaria|montenegro|2026-06-01",
                    "provider": "sstats",
                    "home_team": "Bulgaria",
                    "away_team": "Montenegro",
                    "commence_time": "2026-06-01T16:00:00+00:00",
                },
                {
                    "match_key": "soccer|bulgaria|montenegro|2026-06-01",
                    "provider": "football_data",
                    "home_team": "Bulgaria",
                    "away_team": "Montenegro",
                    "commence_time": "2026-06-01T16:00:00+00:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    fallback._CONTEXT_SOURCE_INDEX_CACHE = None

    sources, meta = fallback.candidate_confirmation_sources(
        {
            "match_key": "soccer|bulgaria|montenegro|2026-06-01",
            "family": "totals",
        }
    )

    assert sources == ["football_data", "sstats"]
    assert meta["weather_neutral"] is True
