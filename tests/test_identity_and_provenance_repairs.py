from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.schemas import Match
from app.services.match_identity import MatchIdentity, best_identity_match, score_match_identity
from app.services.normalizer import dedupe_matches

UTC = timezone.utc


def _identity(provider: str, event_id: str, offset_hours: float = 0.0) -> MatchIdentity:
    return MatchIdentity(
        provider=provider,
        provider_event_id=event_id,
        sport_key="soccer",
        home="Alpha FC",
        away="Beta United",
        league="Premier League",
        start_utc=(datetime(2026, 8, 8, 15, tzinfo=UTC) + timedelta(hours=offset_hours)).isoformat(),
    )


def _match(source: str, event_id: str, metadata: dict) -> Match:
    kickoff = datetime(2026, 8, 8, 15, tzinfo=UTC)
    return Match(
        source=source,
        source_event_id=event_id,
        sport_key="soccer",
        league_name="Premier League",
        home_team="Alpha FC",
        away_team="Beta United",
        commence_time=kickoff,
        home_team_norm="alpha",
        away_team_norm="beta united",
        league_key="premier league",
        metadata=metadata,
    )


def test_same_provider_event_id_is_authoritative() -> None:
    score = score_match_identity(_identity("provider", "event-1"), _identity("provider", "event-1", 6))
    assert score.quality == "exact"
    assert score.score == 100.0
    assert "provider_event_id_exact" in score.reasons


def test_conflicting_same_provider_event_ids_are_rejected() -> None:
    score = score_match_identity(_identity("provider", "event-1"), _identity("provider", "event-2"))
    assert score.quality == "reject"
    assert "provider_event_id_conflict" in score.reasons


def test_ambiguous_name_only_match_is_rejected() -> None:
    reference = _identity("inventory", "")
    first = _identity("provider-a", "", 1)
    second = _identity("provider-b", "", 1)
    match, score = best_identity_match(reference, [first, second])
    assert match is None
    assert score.quality == "reject"
    assert any(reason.startswith("ambiguous_identity_margin:") for reason in score.reasons)


def test_dedupe_preserves_all_provider_event_ids() -> None:
    first = _match("odds_api_io", "odds-1", {"odds_field": "price"})
    second = _match("sportlogic", "sport-1", {"fixture_id": "fixture-1"})
    merged = dedupe_matches([first, second])
    assert len(merged) == 1
    metadata = merged[0].metadata
    assert metadata["provider_source_ids"] == {"odds_api_io": "odds-1", "sportlogic": "sport-1"}
    assert set(metadata["sources_seen"]) == {"odds_api_io", "sportlogic"}
    assert len(metadata["provider_records"]) == 2
