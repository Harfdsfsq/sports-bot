from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.providers import odds_api_io
from app.providers import odds_api_io_fast_match_patch

UTC = timezone.utc


def _match(home: str, away: str, kickoff: datetime):
    return SimpleNamespace(
        sport_key="soccer",
        home_team=home,
        away_team=away,
        commence_time=kickoff,
        league_name="Test League",
        match_key=f"soccer|{home}|{away}|{kickoff.date().isoformat()}",
        tier="mid",
    )


def test_exact_event_uses_small_shortlist() -> None:
    odds_api_io_fast_match_patch.install()
    settings = SimpleNamespace(
        odds_api_io_per_run_max=8,
        fallback_match_start_tolerance_hours=8,
        match_start_tolerance_hours=12,
    )
    provider = odds_api_io.OddsApiIoProvider(settings)
    kickoff = datetime(2026, 7, 17, 15, 0, tzinfo=UTC)
    target = _match("Jagiellonia Bialystok", "FC Nordsjaelland", kickoff)
    matches = [
        _match(f"Decoy Home {index}", f"Decoy Away {index}", kickoff + timedelta(minutes=index % 30))
        for index in range(300)
    ]
    matches.append(target)
    event = {
        "home": "Jagiellonia Bialystok",
        "away": "FC Nordsjaelland",
        "league": "Test League",
        "commence_time": kickoff,
    }

    result = provider._match_event(event, matches)

    assert result is target
    stats = provider._harizon_fast_match_stats
    assert stats["exact_shortlists"] == 1
    assert stats["shortlist_candidates"] == 1
    assert stats["original_candidates"] == len(matches)
    assert stats["max_shortlist"] == 1


def test_unrelated_event_skips_expensive_fuzzy_scan() -> None:
    odds_api_io_fast_match_patch.install()
    settings = SimpleNamespace(
        odds_api_io_per_run_max=8,
        fallback_match_start_tolerance_hours=8,
        match_start_tolerance_hours=12,
    )
    provider = odds_api_io.OddsApiIoProvider(settings)
    kickoff = datetime(2026, 7, 17, 15, 0, tzinfo=UTC)
    matches = [_match(f"Alpha {index}", f"Beta {index}", kickoff) for index in range(300)]
    event = {
        "home": "Completely Unrelated Home",
        "away": "Completely Unrelated Away",
        "league": "Other League",
        "commence_time": kickoff,
    }

    result = provider._match_event(event, matches)

    assert result is None
    stats = provider._harizon_fast_match_stats
    assert stats["no_shortlist"] == 1
    assert stats["shortlist_candidates"] == 0
