from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_repair_removes_current_run_api_coverage_only_rows(tmp_path: Path) -> None:
    root = tmp_path
    exports = root / ".data" / "exports"
    exports.mkdir(parents=True)
    ledger = root / ".data" / "prediction-ledger.jsonl"
    ledger.parent.mkdir(parents=True)

    rows = [
        {
            "ledger_id": "123|real",
            "run_id": "123",
            "status": "rejected_or_watch",
            "stage_seen": ["fallback", "value_patch"],
            "home_team": "A",
            "away_team": "B",
            "odds": 2.1,
            "ev_pct": 3.0,
            "edge_pp": 1.4,
            "reasons": ["xg_direction_conflict"],
        },
        {
            "ledger_id": "123|coverage-only",
            "run_id": "123",
            "status": "rejected_or_watch",
            "stage_seen": ["api_coverage"],
            "home_team": "C",
            "away_team": "D",
            "odds": 3.8,
            "ev_pct": 80.0,
            "edge_pp": None,
            "reasons": [],
        },
        {
            "ledger_id": "old|coverage-only",
            "run_id": "old",
            "status": "rejected_or_watch",
            "stage_seen": ["api_coverage"],
            "home_team": "Old",
            "away_team": "Row",
            "odds": 3.8,
            "ev_pct": 80.0,
            "edge_pp": None,
            "reasons": [],
        },
    ]
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (exports / "latest-prediction-ledger-summary.json").write_text(json.dumps({"current_run_id": "123"}), encoding="utf-8")
    (exports / "latest-prediction-calibration-audit.json").write_text(json.dumps({
        "run_id": "123",
        "counts": {"candidate_keys": 2},
        "rows": [
            {"home_team": "A", "away_team": "B", "odds": 2.1, "stage_seen": {"fallback": True}, "reasons": ["x"]},
            {"home_team": "C", "away_team": "D", "odds": 3.8, "stage_seen": {"api_coverage": True}, "reasons": []},
        ],
    }), encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "scripts" / "repair_prediction_accumulation_outputs.py"
    env = os.environ.copy()
    env["GITHUB_RUN_ID"] = "123"
    result = subprocess.run([sys.executable, str(script)], cwd=root, env=env, text=True, capture_output=True, check=True)
    assert "coverage_only_rows_removed" in result.stdout

    repaired_rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r["ledger_id"] for r in repaired_rows] == ["123|real", "old|coverage-only"]

    summary = json.loads((exports / "latest-prediction-ledger-summary.json").read_text(encoding="utf-8"))
    assert summary["current_run_rows"] == 1
    assert summary["rows_missing_core_metrics_current_run"] == 0
    assert summary["repair_removed_current_run_coverage_only_rows"] == 1

    calibration = json.loads((exports / "latest-prediction-calibration-audit.json").read_text(encoding="utf-8"))
    assert calibration["counts"]["candidate_keys"] == 1
    assert calibration["counts"]["coverage_only_rows_removed_by_repair"] == 1
