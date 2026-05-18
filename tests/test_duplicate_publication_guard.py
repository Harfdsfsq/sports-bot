from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.schemas import CandidateBet
from app.services.publication_lifecycle import candidate_dedupe_keys, load_sent_candidate_keys
from app.services.runner import PredictionRunner


def make_candidate() -> CandidateBet:
    return CandidateBet(
        match_key="soccer|huesca|leganes|2026-05-18",
        sport_key="soccer",
        league_name="Spain - LaLiga 2",
        home_team="CD Leganes",
        away_team="SD Huesca",
        commence_time=datetime(2026, 5, 18, 18, 30, tzinfo=timezone.utc),
        family="totals",
        selection="Больше",
        selection_key="over",
        odds=3.29,
        fair_odds=2.22,
        implied_probability=1 / 3.29,
        market_probability=0.3096,
        consensus_probability=0.3096,
        model_probability=0.6986,
        final_probability=0.4501,
        adjusted_probability=0.4501,
        edge_pct=14.05,
        ev_pct=48.09,
        confidence=73.7,
        books_count=3,
        sources_count=2,
        point=3.5,
        stake_amount=60.0,
        stake_pct=6.0,
        source_summary={
            "odds_sources": ["bzzoiro", "odds_api_io"],
            "odds_sources_count": 2,
            "context_sources": ["sstats", "bzzoiro"],
            "context_sources_count": 2,
        },
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "point": 3.5},
            {"source": "bzzoiro", "bookmaker": "Bzzoiro", "family": "totals", "point": 3.5},
        ],
    )


def test_republish_seen_candidates_disabled_by_default() -> None:
    assert Settings().republish_seen_candidates_when_empty is False


def test_publishable_filter_blocks_previously_sent_semantic_key(tmp_path) -> None:
    settings = Settings(state_path=str(tmp_path / "state.json"), debug_path=str(tmp_path / "debug.json"))
    runner = PredictionRunner(settings)
    candidate = make_candidate()
    keys = candidate_dedupe_keys(candidate)
    runner._seen_published_fingerprints = set(keys)

    publishable = runner._filter_publishable_candidates([candidate])

    assert publishable == []
    assert "publish_blocked=already_telegram_sent_semantic_dedupe" in candidate.reasons
    assert candidate.source_summary["publication_blocked_reason"] == "already_telegram_sent_semantic_dedupe"


def test_sent_key_loader_matches_latest_bets_without_commence_time(tmp_path) -> None:
    candidate = make_candidate()
    latest_bets = tmp_path / "latest-bets.json"
    latest_bets.write_text(json.dumps([
        {
            "match_key": "soccer|huesca|leganes|2026-05-18",
            "sport_key": "soccer",
            "home_team": "CD Leganes",
            "away_team": "SD Huesca",
            "commence_time_utc": "2026-05-18T18:30:00+00:00",
            "family": "totals",
            "selection": "Больше",
            "selection_key": "over",
            "point": 3.5,
            "status": "pending",
            "telegram_sent": True,
            "publication_lifecycle_status": "telegram_sent",
        }
    ], ensure_ascii=False), encoding="utf-8")

    seen = load_sent_candidate_keys([latest_bets])

    assert candidate_dedupe_keys(candidate).intersection(seen)
