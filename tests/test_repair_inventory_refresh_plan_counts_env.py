from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("repair_inventory_refresh_plan_counts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_helpers_are_safe(monkeypatch):
    module = load_module(Path("scripts/repair_inventory_refresh_plan_counts.py"))
    monkeypatch.delenv("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", raising=False)
    assert module.env_int("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", 360) == 360
    monkeypatch.setenv("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", "bad")
    assert module.env_int("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", 360) == 360
    monkeypatch.setenv("ALLOW_STALE_DEBUG_TIME_FOR_INVENTORY", "true")
    assert module.env_bool("ALLOW_STALE_DEBUG_TIME_FOR_INVENTORY", False) is True


def test_now_utc_from_debug_does_not_raise_without_env(tmp_path, monkeypatch):
    module = load_module(Path("scripts/repair_inventory_refresh_plan_counts.py"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".logs").mkdir()
    now = datetime.now(UTC)
    (tmp_path / ".logs" / "debug-last-run.json").write_text(
        json.dumps({"summary": {"current_time_utc": now.isoformat()}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", raising=False)
    monkeypatch.delenv("ALLOW_STALE_DEBUG_TIME_FOR_INVENTORY", raising=False)
    got = module.now_utc_from_debug()
    assert abs((got - now).total_seconds()) < 5


@pytest.mark.parametrize("case", ["fresh", "stale", "future", "replay", "malformed"])
def test_debug_clock_rejects_stale_future_and_malformed_artifacts(tmp_path, monkeypatch, case):
    module = load_module(Path("scripts/repair_inventory_refresh_plan_counts.py"))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    now = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setenv("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", "30")
    monkeypatch.setenv("ALLOW_STALE_DEBUG_TIME_FOR_INVENTORY", "true" if case == "replay" else "false")
    age = {"fresh": 10, "stale": 1440, "future": -1440, "replay": 1440, "malformed": 0}[case]
    observed = now - timedelta(minutes=age)
    payload = [] if case == "malformed" else {"summary": {"current_time_utc": observed.isoformat()}}
    (tmp_path / ".logs").mkdir()
    (tmp_path / ".logs/debug-last-run.json").write_text(json.dumps(payload))
    expected = observed if case in {"fresh", "replay"} else now
    assert module.now_utc_from_debug() == expected
