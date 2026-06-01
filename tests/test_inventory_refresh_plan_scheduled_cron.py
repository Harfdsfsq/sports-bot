from __future__ import annotations

import importlib
import json
from pathlib import Path
from uuid import uuid4


def test_inventory_refresh_plan_uses_scheduled_cron_for_no_more_runs(monkeypatch):
    base = Path(".data") / "test-inventory-refresh-plan" / uuid4().hex
    monkeypatch.setenv("HARIZON_RUN_NOW_UTC", "2026-06-01T14:23:00+00:00")  # 17:23 MSK
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-06-01")
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("LINE_MOVEMENT_CRON_INTERVAL_MINUTES", "120")
    monkeypatch.setenv("MIN_KICKOFF_LEAD_MINUTES", "30")
    (base / ".data" / "day_inventory").mkdir(parents=True)
    inv = {
        "date_local": "2026-06-01",
        "matches": [
            {
                "match_key": "soccer|maldives|afghanistan|2026-06-01",
                "home_team": "Maldives",
                "away_team": "Afghanistan",
                "league_name": "Friendly",
                "kickoff_utc": "2026-06-01T16:00:00+00:00",  # 19:00 MSK, next slot 18:00 MSK still useful.
                "coverage": {"odds": True, "context": True},
                "refresh": {"last_odds_refresh_utc": "2026-06-01T14:20:00+00:00"},
            }
        ],
    }
    (base / ".data" / "day_inventory" / "2026-06-01.json").write_text(json.dumps(inv), encoding="utf-8")

    mod = importlib.import_module("scripts.update_day_inventory_priority_and_line_state")
    mod.ROOT = base
    mod.EXPORT_DIR = base / ".data" / "exports"
    mod.DAY_INV_DIR = base / ".data" / "day_inventory"
    mod.LINE_HISTORY_DIR = base / ".data" / "line_history"
    mod.OUT_PATH = mod.EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json"
    mod.REFRESH_PLAN_PATH = mod.EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
    mod.LINE_GUARD_REPORT_PATH = mod.EXPORT_DIR / "latest-line-movement-guard-report.json"

    report = mod.update_inventory_priority("2026-06-01", mod.now_utc_from_debug())
    out = json.loads((base / ".data" / "day_inventory" / "2026-06-01.json").read_text(encoding="utf-8"))
    plan = out["matches"][0]["refresh_plan"]

    assert plan["next_scheduled_run_at_utc"] == "2026-06-01T15:00:00+00:00"
    assert plan["has_next_regular_run_before_kickoff"] is True
    assert plan["no_more_regular_run_before_kickoff"] is False
    assert report["no_more_regular_run_before_kickoff"] == 0
