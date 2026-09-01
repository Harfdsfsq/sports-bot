from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.providers.sstats_pari_odds import SStatsPariOddsProvider
from app.services.daily_coverage_full_inventory_provider_patch import (
    _coverage_horizon_matches,
)
from app.services.daily_coverage_ranking import (
    coverage_horizon_days,
    horizon_day_offset,
)
from app.services.strict_inventory_horizon_activation import _real_fixture
from app.services.strict_real_fixture_inventory import _real_fixture as rebuild_real_fixture


def _match(key: str, kickoff: datetime) -> SimpleNamespace:
    return SimpleNamespace(match_key=key, commence_time=kickoff)


def test_ranking_accepts_tomorrows_fixture(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("RUN_DAYS_AHEAD", "2")
    kickoff = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)

    assert coverage_horizon_days() == 2
    assert horizon_day_offset(kickoff, "2026-07-20") == 1


def test_provider_pool_covers_full_horizon(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-20")
    monkeypatch.setenv("RUN_DAYS_AHEAD", "2")
    runner = SimpleNamespace(settings=SimpleNamespace(tzinfo=ZoneInfo("Europe/Moscow")))
    now = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)
    rows = [
        _match("today", datetime(2026, 7, 20, 18, 0, tzinfo=UTC)),
        _match("tomorrow", datetime(2026, 7, 21, 10, 0, tzinfo=UTC)),
        _match("outside", datetime(2026, 7, 22, 10, 0, tzinfo=UTC)),
        _match("finished", datetime(2026, 7, 20, 15, 0, tzinfo=UTC)),
    ]

    selected = _coverage_horizon_matches(runner, rows, now)

    assert [row.match_key for row in selected] == ["today", "tomorrow"]


def test_identity_only_evidence_is_not_a_real_fixture() -> None:
    expander = SimpleNamespace(
        team_value=lambda row, side: str(row.get(f"{side}_team") or ""),
        parse_dt=lambda value: (
            datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        ),
        norm=lambda value: str(value).strip().lower(),
    )
    row = {
        "match_key": "soccer|home|away|2026-07-20",
        "metadata": {"verified_odds_sources": ["a", "b"]},
    }

    assert rebuild_real_fixture(row, expander) is False


def test_strict_selector_requires_teams_and_exact_kickoff() -> None:
    row = {
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_utc": "2026-07-21T10:00:00+00:00",
    }
    sync = SimpleNamespace(
        row_kickoff=lambda value: datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    )

    assert _real_fixture(row, sync) is True
    assert _real_fixture({"match_key": "soccer|home|away|2026-07-21"}, sync) is False


def test_sstats_candidate_dates_include_local_midnight(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    value = datetime(2026, 7, 20, 22, 30, tzinfo=UTC)

    dates = SStatsPariOddsProvider._candidate_dates(value)

    assert {"2026-07-20", "2026-07-21", "2026-07-22"}.issubset(dates)
