from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services import top_inventory_runtime_scope_patch as scope


def _row(i: int, kickoff: str) -> dict:
    return {
        "date_local": "2026-05-31",
        "match_key": f"2026-05-31|old_home{i}|old_away{i}",
        "home_team": f"Old Home {i}",
        "away_team": f"Old Away {i}",
        "league_name": "Old League",
        "kickoff_utc": kickoff,
    }


def _future_match(i: int) -> dict:
    return {
        "source": "odds_api_io",
        "source_event_id": str(i),
        "sport_key": "soccer",
        "match_key": f"soccer|future_home_{i}|future_away_{i}|2026-06-01",
        "home_team": f"Future Home {i}",
        "away_team": f"Future Away {i}",
        "league_name": "Future League",
        "commence_time": "2026-05-31T22:00:00+00:00",
    }


def test_scope_result_creates_runtime_topup_when_frozen_roster_has_no_future(monkeypatch, tmp_path):
    day = tmp_path / "day_inventory"
    out = tmp_path / "exports"
    day.mkdir()
    out.mkdir()
    monkeypatch.setattr(scope, "DAY_INV_DIR", day)
    monkeypatch.setattr(scope, "EXPORT_DIR", out)
    monkeypatch.setattr(scope, "REPORT_PATH", out / "latest-top-inventory-runtime-scope.json")
    monkeypatch.setattr(scope, "PROGRESSIVE_STATE_PATH", day / "progressive_coverage_state.json")
    monkeypatch.setattr(scope, "PROGRESSIVE_EXPORT_PATH", out / "latest-progressive-coverage-state.json")
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-31")
    monkeypatch.setenv("TOP_INVENTORY_RUNTIME_MAX_MATCHES", "300")
    monkeypatch.setenv("TOP_INVENTORY_RUNTIME_MIN_FUTURE_ROWS", "5")
    stale_rows = [_row(i, "2026-05-31T12:00:00+00:00") for i in range(300)]
    (day / "2026-05-31.json").write_text(json.dumps({"date_local": "2026-05-31", "matches": stale_rows}), encoding="utf-8")
    (day / "frozen_inventory_roster_2026-05-31.json").write_text(json.dumps({"date_local": "2026-05-31", "matches": stale_rows}), encoding="utf-8")

    raw_future = [_future_match(i) for i in range(12)]
    scoped, report = scope._scope_result("fetch_matches", raw_future, now_utc=datetime(2026, 5, 31, 15, 34, tzinfo=timezone.utc))

    assert len(scoped) == 12
    assert report["runtime_topup_report"]["created"] is True
    topup = json.loads((day / "runtime_topup_roster_2026-05-31.json").read_text(encoding="utf-8"))
    assert len(topup["matches"]) == 12
    assert topup["matches"][0]["home_team"].startswith("Future Home")
