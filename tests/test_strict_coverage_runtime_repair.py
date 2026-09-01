from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.schemas import Match, MatchContext
from app.services import daily_coverage_ledger as ledger
from app.services.strict_coverage_runtime_repair import (
    _alias_cached_provider_data_factory,
    _prediction_event_id,
    score_event_match_compat,
)


def _match() -> Match:
    return Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Premier League",
        home_team="Home FC",
        away_team="Away FC",
        commence_time=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        home_team_norm="home",
        away_team_norm="away",
        league_key="premier-league",
    )


def test_score_event_match_compat_accepts_legacy_positional_signature() -> None:
    start = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    score, quality = score_event_match_compat(
        "soccer",
        "Home FC",
        "Away FC",
        start,
        "Premier League",
        "Home",
        "Away",
        start,
        "Premier League",
        exact_tolerance_hours=6.0,
        fuzzy_tolerance_hours=24.0,
    )
    assert score > 90
    assert quality in {"exact", "loose"}


def test_alias_cache_reuses_canonical_day_evidence(monkeypatch, tmp_path: Path) -> None:
    match = _match()
    context = MatchContext(
        source="clubelo",
        payload={"strength_delta": 42},
        expected_home=1.45,
        expected_away=1.05,
    )
    evidence_path = tmp_path / "daily-coverage-evidence-2026-07-18.json"
    evidence_path.write_text(
        json.dumps(
            {
                "date_local": "2026-07-18",
                "matches": {
                    "2026-07-18|Away|Home": {
                        "context": {
                            "clubelo": {
                                "updated_at_utc": datetime.now(UTC).isoformat(),
                                "data": asdict(context),
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ledger, "evidence_path", lambda _day: evidence_path)
    monkeypatch.setattr(ledger, "target_date", lambda: "2026-07-18")

    alias_aware = _alias_cached_provider_data_factory(lambda *_args, **_kwargs: {})
    cached = alias_aware("clubelo", "fetch_context", [match])

    assert cached[match.match_key].expected_home == 1.45
    assert cached[match.match_key].source == "clubelo"


def test_prediction_event_id_supports_v2_nested_event() -> None:
    assert _prediction_event_id({"id": 9, "event": {"id": 204851}}) == "204851"
    assert _prediction_event_id({"event_id": 997}) == "997"
