from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.services import strict_inventory_horizon_activation as activation


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _fake_sync(rows_by_path: dict[str, list[dict[str, Any]]]) -> SimpleNamespace:
    def identity(row: dict[str, Any], _day: str):
        kickoff = _parse(row.get("kickoff_utc"))
        return (kickoff.date().isoformat(), row["home_team"], row["away_team"]) if kickoff else None

    return SimpleNamespace(
        _candidate_paths=lambda _day: list(rows_by_path),
        load=lambda path, _default: {"date_local": "2026-07-20", "matches": rows_by_path[str(path)]},
        _rows=lambda payload: [dict(row) for row in payload.get("matches", [])],
        _identity=identity,
        row_kickoff=lambda row: _parse(row.get("kickoff_utc")),
        row_identities=lambda row, kickoff: {
            (kickoff.date().isoformat(), row["home_team"], row["away_team"])
        }
        if kickoff
        else set(),
        identity_from_key=lambda _key: None,
        row_key=lambda row: str(row.get("match_key") or ""),
    )


def test_row_horizon_uses_local_date_not_utc_date(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("RUN_DAYS_AHEAD", "2")
    sync = _fake_sync({})

    local_day_one = {"kickoff_utc": "2026-07-19T22:30:00+00:00"}
    local_day_two = {"kickoff_utc": "2026-07-20T22:30:00+00:00"}
    outside = {"kickoff_utc": "2026-07-21T22:30:00+00:00"}

    assert activation.row_in_horizon(local_day_one, "2026-07-20", sync) is True
    assert activation.row_in_horizon(local_day_two, "2026-07-20", sync) is True
    assert activation.row_in_horizon(outside, "2026-07-20", sync) is False


def test_candidate_factory_keeps_full_configured_horizon(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("RUN_DAYS_AHEAD", "2")
    rows = {
        "inventory.json": [
            {
                "match_key": "m1",
                "home_team": "Home One",
                "away_team": "Away One",
                "kickoff_utc": "2026-07-19T22:30:00+00:00",
            },
            {
                "match_key": "m2",
                "home_team": "Home Two",
                "away_team": "Away Two",
                "kickoff_utc": "2026-07-20T22:30:00+00:00",
            },
            {
                "match_key": "m3",
                "home_team": "Home Three",
                "away_team": "Away Three",
                "kickoff_utc": "2026-07-21T22:30:00+00:00",
            },
        ]
    }
    sync = _fake_sync(rows)

    selected = activation._candidate_rows_factory(sync)("2026-07-20")

    assert {row["match_key"] for row in selected} == {"m1", "m2"}
