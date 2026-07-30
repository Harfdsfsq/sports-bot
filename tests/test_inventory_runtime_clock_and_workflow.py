from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path


def test_inventory_priority_ignores_stale_debug_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".logs").mkdir()
    (tmp_path / ".logs" / "debug-last-run.json").write_text(
        json.dumps({"summary": {"current_time_utc": "2026-04-26T22:32:01+00:00"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HARIZON_RUN_NOW_UTC", "2026-05-22T15:11:00+00:00")

    mod = importlib.import_module("scripts.update_day_inventory_priority_and_line_state")
    now = mod.now_utc_from_debug()

    assert now == datetime(2026, 5, 22, 15, 11, tzinfo=UTC)


def test_inventory_priority_recomputes_near_kickoff_minutes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARIZON_RUN_NOW_UTC", "2026-05-22T15:11:00+00:00")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-22")
    (tmp_path / ".data" / "day_inventory").mkdir(parents=True)
    inv = {
        "date_local": "2026-05-22",
        "matches": [
            {
                "match_key": "soccer|qarabag|sumqayit|2026-05-22",
                "home_team": "Sumqayit FK",
                "away_team": "Qarabag FK",
                "league_name": "Azerbaijan - Premier League",
                "kickoff_utc": "2026-05-22T15:30:00+00:00",
                "coverage": {"odds": True, "context": False},
                "refresh": {"last_odds_refresh_utc": None},
            }
        ],
    }
    (tmp_path / ".data" / "day_inventory" / "2026-05-22.json").write_text(json.dumps(inv), encoding="utf-8")

    mod = importlib.import_module("scripts.update_day_inventory_priority_and_line_state")
    mod.ROOT = tmp_path
    mod.EXPORT_DIR = tmp_path / ".data" / "exports"
    mod.DAY_INV_DIR = tmp_path / ".data" / "day_inventory"
    mod.LINE_HISTORY_DIR = tmp_path / ".data" / "line_history"
    mod.OUT_PATH = mod.EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json"
    mod.REFRESH_PLAN_PATH = mod.EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
    mod.LINE_GUARD_REPORT_PATH = mod.EXPORT_DIR / "latest-line-movement-guard-report.json"
    report = mod.update_inventory_priority("2026-05-22", mod.now_utc_from_debug())
    out = json.loads((tmp_path / ".data" / "day_inventory" / "2026-05-22.json").read_text(encoding="utf-8"))
    row = out["matches"][0]

    assert row["minutes_to_kickoff"] == 19.0
    assert row["pre_kickoff_status"] == "too_soon"
    assert report["top_priority_matches"][0]["minutes_to_kickoff"] == 19.0


def test_workflow_runs_inventory_line_state_before_controlled_fallback():
    workflow = Path(".github/workflows/run-bot.yml").read_text(encoding="utf-8")
    run_pos = workflow.index("name: Run bot")
    diagnostics_pos = workflow.index("name: Persist movement watchlist and diagnostics")
    fallback_pos = workflow.index("name: Evaluate controlled fallback publication")
    assert run_pos < diagnostics_pos
    assert diagnostics_pos < fallback_pos
    assert "scripts/restore_awaiting_movement_candidates.py" in workflow
    assert "scripts/persist_awaiting_movement_candidates.py" in workflow
    assert "scripts/publish_controlled_fallback_guarded.py" in workflow
    assert "publish_controlled_fallback_guarded.py || python -u scripts/publish_controlled_fallback.py" not in workflow


def test_run_bot_backfills_inventory_after_dependencies():
    workflow = Path(".github/workflows/run-bot.yml").read_text(encoding="utf-8")
    install_pos = workflow.index("name: Install dependencies")
    backfill_pos = workflow.index("name: Backfill day inventory to target")
    policy_pos = workflow.index("name: Apply publication family policy")

    assert install_pos < backfill_pos < policy_pos
    assert "scripts/build_day_inventory_core_v3.py" in workflow
    assert "latest-day-inventory-target-expand.json" in workflow


def test_remote_cronjob_owns_regular_and_inventory_schedules():
    for workflow_name in ("run-bot.yml", "build-day-inventory.yml"):
        workflow = Path(".github/workflows", workflow_name).read_text(encoding="utf-8")
        assert "  schedule:" not in workflow
        assert "cron:" not in workflow
        assert "workflow_dispatch:" in workflow


def test_inventory_workflow_uses_core_rule_providers():
    workflow = Path(".github/workflows/build-day-inventory.yml").read_text(encoding="utf-8")
    assert 'DAY_INVENTORY_ENABLE_BZZOIRO: "true"' in workflow
    assert 'DAY_INVENTORY_ENABLE_SSTATS: "true"' in workflow
    assert 'DAY_INVENTORY_ENABLE_SPORTLOGIC: "true"' in workflow
    assert 'ENABLE_ODDS_API_IO: "true"' in workflow
    assert 'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN: "4"' in workflow


def test_run_bot_workflow_uses_rules_b_tier_and_sportlogic():
    workflow = Path(".github/workflows/run-bot.yml").read_text(encoding="utf-8")
    assert 'PUBLISH_TIER_B_MIN_ODDS_SOURCES: "1"' in workflow
    assert 'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES: "1"' in workflow
    assert 'DAY_INVENTORY_FORCE_FULL_300: "true"' in workflow
    assert 'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES: "1"' in workflow
    assert 'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES: "1"' in workflow
    assert 'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES: "1"' in workflow
    assert 'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM: "false"' in workflow
    assert 'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM: "false"' in workflow
    assert 'SPORTLOGIC_ENABLED: "true"' in workflow
    assert 'ENABLE_SPORTLOGIC: "true"' in workflow
    assert 'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN: "4"' in workflow
    assert "latest-run-bot-step-status.json" in workflow
    assert 'exit "${run_status}"' in workflow
