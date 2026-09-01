from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services import daily_coverage_common as common
from app.services import daily_coverage_plan as plan


def _rows(count: int, start: datetime) -> list[dict[str, object]]:
    return [
        {
            "match_key": f"soccer|home-{index}|away-{index}|{(start + timedelta(minutes=index)).isoformat()}",
            "home_team": f"Home {index}",
            "away_team": f"Away {index}",
            "league_name": "Senior League",
            "kickoff_utc": (start + timedelta(minutes=index)).isoformat(),
            "priority": 1000 - index,
            "odds_sources": ["odds_api_io_account1", "odds_api_io_account2"],
            "context_sources": ["sstats", "provider_day_discovery_canonical_pool"],
        }
        for index in range(count)
    ]


def _paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    day_dir = tmp_path / ".data" / "day_inventory"
    export_dir = tmp_path / ".data" / "exports"
    day_dir.mkdir(parents=True)
    export_dir.mkdir(parents=True)
    monkeypatch.setattr(common, "DAY_DIR", day_dir)
    monkeypatch.setattr(common, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(common, "PLAN_PATH", export_dir / "latest-daily-coverage-plan.json")
    monkeypatch.setattr(common, "LEDGER_PATH", export_dir / "latest-daily-coverage-ledger.json")
    monkeypatch.setattr(plan, "DAY_DIR", day_dir)
    monkeypatch.setattr(plan, "PLAN_PATH", export_dir / "latest-daily-coverage-plan.json")
    monkeypatch.setattr(plan, "LEDGER_PATH", export_dir / "latest-daily-coverage-ledger.json")
    return day_dir, export_dir


def test_three_unique_runs_expand_target_to_300(monkeypatch, tmp_path: Path) -> None:
    day_dir, _ = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(plan, "focused_alpha_enabled", lambda: False)
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-18")
    now = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    (day_dir / "2026-07-18.json").write_text(
        json.dumps({"date_local": "2026-07-18", "matches": _rows(300, now + timedelta(hours=1))}),
        encoding="utf-8",
    )
    targets = []
    for run_id in ("101", "102", "103"):
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        targets.append(plan.prepare_daily_coverage(now)["phase_cumulative_target"])
    assert targets == [150, 250, 300]


def test_accounts_and_synthetic_context_are_not_independent() -> None:
    assert common.independent_sources(
        ["odds_api_io_account1", "odds_api_io_account2", "sportlogic"], role="odds"
    ) == ["odds_api_io", "sportlogic"]
    assert common.independent_sources(
        ["sstats", "self_history", "provider_day_discovery_canonical_pool", "clubelo"], role="context"
    ) == ["clubelo", "sstats"]


def test_provider_backlog_is_broader_than_focused_model_scope(monkeypatch) -> None:
    monkeypatch.setattr(plan, "focused_alpha_enabled", lambda: True)
    monkeypatch.setenv("HARIZON_FULL_INVENTORY_PROVIDER_TARGETS", "300")
    monkeypatch.setenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS", "36")
    ranked = [
        {
            "match_key": "far-covered",
            "hours_to_kickoff": 10.0,
            "odds_sources_count": 2,
            "context_sources_count": 2,
            "line_deficit": 0,
            "context_deficit": 0,
        },
        {
            "match_key": "near-covered",
            "hours_to_kickoff": 2.0,
            "odds_sources_count": 2,
            "context_sources_count": 2,
            "line_deficit": 0,
            "context_deficit": 0,
        },
        {
            "match_key": "near-empty",
            "hours_to_kickoff": 2.5,
            "odds_sources_count": 0,
            "context_sources_count": 0,
            "line_deficit": 2,
            "context_deficit": 2,
        },
    ]

    targets = plan._provider_coverage_targets(ranked, [ranked[1]])

    assert [row["match_key"] for row in targets] == [
        "near-empty",
        "near-covered",
        "far-covered",
    ]
    assert len(targets) == 3
    assert targets[1]["focused_alpha_model_target"] is True
    assert all(row["provider_coverage_backlog"] for row in targets)
