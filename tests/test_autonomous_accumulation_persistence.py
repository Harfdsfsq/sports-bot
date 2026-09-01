from __future__ import annotations

import json
from pathlib import Path


def test_bounded_json_ledger_is_valid_deduped_and_trimmed(tmp_path, monkeypatch):
    from app.services import autonomous_accumulation_persistence as persistence

    ledger = tmp_path / "latest-autonomous-prediction-ledger.json"
    monkeypatch.setattr(persistence, "PREDICTION_LEDGER", ledger)
    monkeypatch.setenv("AUTONOMOUS_PREDICTION_LEDGER_MAX_ROWS", "3")

    persistence._append_bounded_json(
        ledger,
        [
            {"candidate_id": "a", "run_id": "1", "value": 1},
            {"candidate_id": "b", "run_id": "1", "value": 2},
        ],
    )
    persistence._append_bounded_json(
        ledger,
        [
            {"candidate_id": "a", "run_id": "1", "value": 10},
            {"candidate_id": "c", "run_id": "2", "value": 3},
            {"candidate_id": "d", "run_id": "2", "value": 4},
        ],
    )

    rows = json.loads(ledger.read_text(encoding="utf-8"))
    assert isinstance(rows, list)
    assert len(rows) == 3
    assert [row["candidate_id"] for row in rows] == ["a", "c", "d"]
    assert rows[0]["value"] == 10


def test_install_redirects_runtime_to_flat_latest_files(tmp_path, monkeypatch):
    from app.services import autonomous_accumulation_persistence as persistence
    from app.services import autonomous_accumulation_runtime as runtime

    export = tmp_path / ".data" / "exports"
    monkeypatch.setattr(persistence, "_INSTALLED", False)
    monkeypatch.setattr(persistence, "EXPORT", export)
    monkeypatch.setattr(persistence, "LEGACY_OUT", export / "autonomous-accumulation")
    monkeypatch.setattr(persistence, "COVERAGE", export / "latest-autonomous-coverage-matrix.json")
    monkeypatch.setattr(persistence, "COVERAGE_LEDGER", export / "latest-autonomous-coverage-run-ledger.json")
    monkeypatch.setattr(persistence, "PREDICTION_LEDGER", export / "latest-autonomous-prediction-ledger.json")
    monkeypatch.setattr(persistence, "LATEST", export / "latest-autonomous-accumulation-report.json")
    monkeypatch.setattr(persistence, "POLICY_REPORT", export / "latest-autonomous-persistence-policy.json")
    monkeypatch.setenv("HARIZON_AUTONOMOUS_ACCUMULATION_MODE", "true")

    result = persistence.install()

    assert result["status"] == "installed"
    assert runtime.OUT == export
    assert runtime.COVERAGE == persistence.COVERAGE
    assert runtime.COVERAGE_LEDGER == persistence.COVERAGE_LEDGER
    assert runtime.PREDICTION_LEDGER == persistence.PREDICTION_LEDGER
    assert runtime.LATEST == persistence.LATEST
    assert runtime._append is persistence._append_bounded_json
    assert persistence.POLICY_REPORT.exists()
    policy = json.loads(persistence.POLICY_REPORT.read_text(encoding="utf-8"))
    assert policy["workflow_change_required"] is False
    assert all(Path(path).name.startswith("latest-") for path in policy["paths"].values())
