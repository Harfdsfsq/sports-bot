from __future__ import annotations

from pathlib import Path

from app.services.runtime_preflight import RuntimePreflight


def test_runtime_preflight_has_nonfatal_phase_policy(tmp_path: Path) -> None:
    preflight = RuntimePreflight(export_dir=tmp_path)
    payload = preflight.apply_phase_policy("run-once")

    assert payload["stage"] == "phase_policy"
    assert payload["phase"] == "run-once"
    assert (tmp_path / "latest-runtime-phase-policy.json").exists()
