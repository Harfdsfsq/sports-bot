from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.services import runbot_discovery_checkpoint_patch as patch


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_incremental_latest_does_not_hide_full_checkpoint(tmp_path, monkeypatch) -> None:
    latest = tmp_path / "latest.json"
    checkpoint = tmp_path / "full-checkpoint.json"
    report = tmp_path / "policy.json"
    artifact = tmp_path / "artifact-policy.json"
    full_payload = {
        "created_at_utc": "2026-07-17T10:00:00+00:00",
        "target_date": "2026-07-17",
        "mode": "runbot_discovery_first_prepare_v5_full_bounded",
        "status": "ok",
    }
    _write(latest, full_payload)

    target = SimpleNamespace()
    target.LATEST_JSON_OUT = latest

    def previous_full_prepare(now=None):
        payload = json.loads(Path(target.LATEST_JSON_OUT).read_text(encoding="utf-8"))
        return {
            "reusable": "incremental" not in str(payload.get("mode") or ""),
            "mode": payload.get("mode"),
            "created_at_utc": payload.get("created_at_utc"),
        }

    target.previous_full_prepare = previous_full_prepare
    target.main = lambda: 0

    monkeypatch.setattr(patch, "CHECKPOINT_PATH", checkpoint)
    monkeypatch.setattr(patch, "REPORT_PATH", report)
    monkeypatch.setattr(patch, "ARTIFACT_REPORT_PATH", artifact)

    result = patch.install_on(target)
    assert result["status"] == "installed"
    assert checkpoint.exists()

    _write(
        latest,
        {
            "created_at_utc": "2026-07-17T11:00:00+00:00",
            "target_date": "2026-07-17",
            "mode": "runbot_discovery_first_prepare_v5_incremental_reuse",
            "status": "ok",
        },
    )
    previous = target.previous_full_prepare(datetime(2026, 7, 17, 11, 30, tzinfo=UTC))

    assert previous["reusable"] is True
    assert previous["mode"] == full_payload["mode"]
    assert previous["source"] == "last_successful_full_checkpoint"


def test_only_successful_full_payloads_replace_checkpoint(tmp_path, monkeypatch) -> None:
    latest = tmp_path / "latest.json"
    checkpoint = tmp_path / "full-checkpoint.json"
    monkeypatch.setattr(patch, "CHECKPOINT_PATH", checkpoint)
    monkeypatch.setattr(patch, "REPORT_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(patch, "ARTIFACT_REPORT_PATH", tmp_path / "artifact.json")

    target = SimpleNamespace(LATEST_JSON_OUT=latest)
    target.previous_full_prepare = lambda now=None: {"reusable": False}

    def main() -> int:
        _write(
            latest,
            {
                "mode": "runbot_discovery_first_prepare_v5_incremental_reuse",
                "status": "ok",
            },
        )
        return 0

    target.main = main
    patch.install_on(target)
    target.main()

    assert not checkpoint.exists()
