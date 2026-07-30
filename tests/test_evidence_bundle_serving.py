from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.schemas import Match, MatchContext, Offer
from app.services.evidence import build_context_bundles, build_match_serving
from app.services.model import CandidateFactory


def _match() -> Match:
    return Match(
        source="odds_api_io",
        source_event_id="evt-1",
        sport_key="soccer",
        league_name="Premier League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 6, 1, 18, 0, tzinfo=UTC),
        home_team_norm="home fc",
        away_team_norm="away fc",
        league_key="premier_league",
        tier="high",
    )


def test_context_bundle_preserves_provider_observations_and_serving_counts():
    match = _match()
    observed_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    contexts = {
        "sstats": {
            match.match_key: MatchContext(
                source="sstats",
                payload={"home_recent": [], "away_recent": []},
                expected_home=1.62,
                expected_away=0.94,
                confidence=64.0,
                details={"home_form": 0.7, "away_form": 0.44},
            )
        },
        "football_data": {
            match.match_key: MatchContext(
                source="football_data",
                payload={"standings": True},
                expected_home=1.48,
                expected_away=1.02,
                confidence=66.0,
                details={"home_rank": 3, "away_rank": 9},
            )
        },
    }
    merged = {
        match.match_key: MatchContext(
            source="ensemble",
            payload={},
            expected_home=1.55,
            expected_away=0.98,
            confidence=70.0,
            details={"merged_sources": ["sstats", "football_data"]},
        )
    }
    offers = {
        match.match_key: [
            Offer(source="odds_api_io", bookmaker="Bet365", family="totals", selection="Over", point=2.5, price=1.95),
            Offer(source="bzzoiro", bookmaker="Unibet", family="totals", selection="Over", point=2.5, price=1.97),
        ]
    }

    bundles = build_context_bundles(contexts, merged, observed_at)
    serving = build_match_serving([match], offers, bundles, {}, observed_at)[match.match_key]

    bundle = bundles[match.match_key]
    assert bundle.context_source_count == 2
    assert [item.provider for item in bundle.contexts] == ["football_data", "sstats"]
    assert bundle.merged_context is not None
    assert len(bundle.merged_context.details["context_observations"]) == 2
    assert serving.context_source_count == 2
    assert serving.line_source_count == 2
    assert serving.line_family_count == 1
    assert serving.line_snapshot_count == 2


def test_candidate_factory_accepts_context_bundle_adapter(monkeypatch):
    monkeypatch.setenv("PUBLISH_MIN_ODDS_SOURCES", "2")
    monkeypatch.setenv("TOTALS_MIN_EDGE_PCT", "0.1")
    monkeypatch.setenv("TOTALS_MIN_EV_PCT", "0.1")
    monkeypatch.setenv("TOTALS_OVER25_MIN_EDGE_PCT", "0.1")
    monkeypatch.setenv("TOTALS_OVER25_MIN_EV_PCT", "0.1")
    monkeypatch.setenv("TOTALS_OVER25_MIN_SUM_XG", "2.0")
    monkeypatch.setenv("TOTALS_OVER25_MIN_ADJUSTED_PROBABILITY", "0.45")
    match = _match()
    observed_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    context_maps = {
        "sstats": {
            match.match_key: MatchContext(
                source="sstats",
                payload={},
                expected_home=1.9,
                expected_away=1.0,
                confidence=67.0,
                details={"home_form": 0.74, "away_form": 0.45},
            )
        },
        "football_data": {
            match.match_key: MatchContext(
                source="football_data",
                payload={},
                expected_home=1.8,
                expected_away=1.05,
                confidence=68.0,
                details={"home_rank": 2, "away_rank": 8},
            )
        },
    }
    merged = {
        match.match_key: MatchContext(
            source="ensemble",
            payload={},
            expected_home=1.85,
            expected_away=1.02,
            confidence=72.0,
            details={"merged_sources": ["sstats", "football_data"]},
        )
    }
    offers = {
        match.match_key: [
            Offer(source="odds_api_io", bookmaker="Bet365", family="totals", selection="Over", point=2.5, price=2.02),
            Offer(source="bzzoiro", bookmaker="Unibet", family="totals", selection="Over", point=2.5, price=2.03),
            Offer(source="odds_api_io", bookmaker="Bet365", family="totals", selection="Under", point=2.5, price=1.86),
            Offer(source="bzzoiro", bookmaker="Unibet", family="totals", selection="Under", point=2.5, price=1.85),
        ]
    }

    bundles = build_context_bundles(context_maps, merged, observed_at)
    candidates, rejections, _debug = CandidateFactory(Settings(_env_file=None)).build_candidates(
        [match],
        offers,
        bundles,
        {},
    )

    assert candidates, rejections
    assert candidates[0].source_summary["context_sources_count"] == 2
    assert candidates[0].source_summary["context_observation_count"] == 2
