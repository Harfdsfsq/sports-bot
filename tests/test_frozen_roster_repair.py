from __future__ import annotations

import json
from pathlib import Path

from app.services import top_inventory_runtime_scope_patch as scope
from scripts import build_day_inventory_coverage_truth as truth


def _row(i: int) -> dict:
    return {
        "date_local": "2026-05-31",
        "match_key": f"2026-05-31|home{i}|away{i}",
        "home_team": f"Home {i}",
        "away_team": f"Away {i}",
        "league_name": "Test League",
        "kickoff_utc": "2026-05-31T20:00:00+00:00",
    }


def test_runtime_scope_repairs_tiny_frozen_roster(monkeypatch, tmp_path):
    day = tmp_path / "day_inventory"
    out = tmp_path / "exports"
    day.mkdir()
    out.mkdir()
    monkeypatch.setattr(scope, "DAY_INV_DIR", day)
    monkeypatch.setattr(scope, "EXPORT_DIR", out)
    monkeypatch.setattr(scope, "REPORT_PATH", out / "latest-top-inventory-runtime-scope.json")
    monkeypatch.setenv("TOP_INVENTORY_RUNTIME_MAX_MATCHES", "300")
    rows = [_row(i) for i in range(300)]
    (day / "2026-05-31.json").write_text(json.dumps({"date_local": "2026-05-31", "matches": rows}), encoding="utf-8")
    (day / "frozen_inventory_roster_2026-05-31.json").write_text(json.dumps({"date_local": "2026-05-31", "matches": rows[:3]}), encoding="utf-8")

    report = scope._ensure_frozen_roster("2026-05-31", 300)

    assert report["rows"] == 300
    assert str(report["reason"]).startswith("repair_tiny_existing_roster")
    repaired = json.loads((day / "frozen_inventory_roster_2026-05-31.json").read_text(encoding="utf-8"))
    assert len(repaired["matches"]) == 300


def test_coverage_truth_repairs_tiny_frozen_roster(monkeypatch, tmp_path):
    day = tmp_path / "day_inventory"
    out = tmp_path / "exports"
    day.mkdir()
    out.mkdir()
    monkeypatch.setattr(truth, "DAY_INV_DIR", day)
    monkeypatch.setattr(truth, "EXPORT_DIR", out)
    monkeypatch.setattr(truth, "FROZEN_ROSTER_EXPORT_PATH", out / "latest-day-inventory-frozen-roster.json")
    rows = [_row(i) for i in range(300)]
    inv = {"date_local": "2026-05-31", "matches": rows}
    (day / "frozen_inventory_roster_2026-05-31.json").write_text(json.dumps({"date_local": "2026-05-31", "matches": rows[:3]}), encoding="utf-8")

    repaired, report = truth.apply_frozen_roster(inv, "2026-05-31", "2026-05-31T12:00:00+00:00")

    assert len(repaired["matches"]) == 300
    assert report["repaired"] is True
    assert str(report["repair_reason"]).startswith("repair_tiny_existing_roster")
