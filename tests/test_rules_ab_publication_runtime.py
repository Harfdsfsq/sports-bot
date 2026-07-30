from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.schemas import CandidateBet
from app.services.publication_tiers import classify_publication_tier


def test_rules_b_tier_can_publish_after_final_line_check(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "LINE_MOVEMENT_STATE_PATH",
        str(tmp_path / "line-movement.json"),
    )
    monkeypatch.setenv("PUBLISH_MIN_ODDS_SOURCES", "1")
    monkeypatch.setenv("PUBLISH_MIN_CONTEXT_SOURCES", "1")
    monkeypatch.setenv("PUBLISH_MIN_BOOKS", "2")
    monkeypatch.setenv("PUBLISH_TIER_A_MIN_ODDS_SOURCES", "2")
    monkeypatch.setenv("PUBLISH_TIER_A_MIN_CONTEXT_SOURCES", "2")
    monkeypatch.setenv("PUBLISH_TIER_A_MIN_BOOKS", "2")
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_ODDS_SOURCES", "1")
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES", "1")
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_BOOKS", "2")

    now = datetime(2026, 7, 30, 15, tzinfo=UTC)
    candidate = CandidateBet(
        match_key="soccer|home|away|2026-07-30",
        sport_key="soccer",
        league_name="Senior League",
        home_team="Home",
        away_team="Away",
        commence_time=now + timedelta(minutes=80),
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
        sources_count=1,
        source_summary={
            "sources": ["odds_api_io"],
            "books": ["Bet365", "Unibet"],
            "context_sources": ["sstats"],
        },
        raw_bucket_offers=[
            {
                "source": "odds_api_io",
                "bookmaker": "Bet365",
                "family": "totals",
                "selection": "Over",
                "point": 2.5,
                "price": 2.04,
            },
            {
                "source": "odds_api_io",
                "bookmaker": "Unibet",
                "family": "totals",
                "selection": "Over",
                "point": 2.5,
                "price": 2.05,
            },
        ],
    )
    settings = SimpleNamespace(
        line_movement_next_run_minutes=120,
        min_sources_publish=1,
        min_context_sources_publish=1,
        min_books_publish=2,
    )

    decision = classify_publication_tier(candidate, settings, now=now)

    assert decision.passed is True
    assert decision.tier == "B"
    assert decision.report["odds_sources_count"] == 1
    assert decision.report["bookmakers_or_price_confirmations_count"] == 2
    assert decision.report["context_sources_count"] == 1
    assert decision.report["line_movement"]["status"] == "publish_now_no_next_cron"
