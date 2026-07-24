from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import focused_alpha_runtime_contract as contract


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_same_github_run_does_not_delete_fresh_debug_again(tmp_path, monkeypatch) -> None:
    lifecycle = tmp_path / "lifecycle.json"
    debug = tmp_path / "debug.json"
    monkeypatch.setattr(contract, "RUN_LIFECYCLE", lifecycle)
    monkeypatch.setattr(contract, "DEBUG_PATH", debug)
    monkeypatch.setenv("GITHUB_RUN_ID", "30103448085")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT", raising=False)

    _write(debug, {"old": True})
    first = contract._start_lifecycle_once()
    assert first["status"] == "running"
    assert not debug.exists()

    monkeypatch.delenv("HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT", raising=False)
    _write(debug, {"summary": {"started_time_utc": datetime.now(UTC).isoformat()}})
    second = contract._start_lifecycle_once()

    assert second["status"] == "already_started_same_github_run"
    assert debug.exists()
    assert json.loads(debug.read_text(encoding="utf-8"))["summary"]
    assert json.loads(lifecycle.read_text(encoding="utf-8"))["started_at_utc"] == first["started_at_utc"]


def test_new_attempt_starts_new_lifecycle_and_clears_old_debug(tmp_path, monkeypatch) -> None:
    lifecycle = tmp_path / "lifecycle.json"
    debug = tmp_path / "debug.json"
    monkeypatch.setattr(contract, "RUN_LIFECYCLE", lifecycle)
    monkeypatch.setattr(contract, "DEBUG_PATH", debug)
    monkeypatch.setenv("GITHUB_RUN_ID", "30103448085")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.delenv("HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT", raising=False)
    _write(
        lifecycle,
        {
            "status": "running",
            "started_at_utc": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "github_run_id": "30103448085",
            "github_run_attempt": "1",
        },
    )
    _write(debug, {"attempt": 1})

    result = contract._start_lifecycle_once()

    assert result["status"] == "running"
    assert result["github_run_attempt"] == "2"
    assert not debug.exists()


def test_recent_full_discovery_reused_above_inventory_floor(monkeypatch) -> None:
    target = SimpleNamespace(
        inventory_matches=lambda: 296,
        env_int=lambda name, default: {
            "DAY_INVENTORY_TARGET_SIZE": 300,
            "RUNBOT_DISCOVERY_FIRST_REUSE_MIN_INVENTORY_ROWS": 260,
        }.get(name, default),
        _target_date=lambda: "2026-07-24",
    )
    result = contract._relax_discovery_reuse(
        target,
        {
            "reusable": False,
            "mode": "runbot_discovery_first_prepare_v5_full_bounded",
            "status": "ok_budget_limited",
            "age_minutes": 80.62,
            "refresh_interval_minutes": 360,
            "target_date": "2026-07-24",
        },
    )

    assert result["reusable"] is True
    assert result["reuse_reason"] == "fresh_full_checkpoint_inventory_floor"
    assert result["inventory_topup_required"] is True
    assert result["current_inventory_matches"] == 296


def test_discovery_reuse_stays_blocked_below_floor() -> None:
    target = SimpleNamespace(
        inventory_matches=lambda: 120,
        env_int=lambda name, default: {
            "DAY_INVENTORY_TARGET_SIZE": 300,
            "RUNBOT_DISCOVERY_FIRST_REUSE_MIN_INVENTORY_ROWS": 260,
        }.get(name, default),
        _target_date=lambda: "2026-07-24",
    )
    result = contract._relax_discovery_reuse(
        target,
        {
            "reusable": False,
            "mode": "runbot_discovery_first_prepare_v5_full_bounded",
            "status": "ok",
            "age_minutes": 30,
            "refresh_interval_minutes": 360,
            "target_date": "2026-07-24",
        },
    )

    assert result["reusable"] is False
    assert result["current_inventory_matches"] == 120
