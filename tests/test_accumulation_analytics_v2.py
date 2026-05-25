from __future__ import annotations

import json
import os
import runpy
import pytest
from pathlib import Path


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_calibration_audit_merges_sparse_quality_with_rich_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    export = tmp_path / ".data" / "exports"
    write_json(export / "latest-quality-consensus-safe-relief.json", {
        "rows": [{
            "match_key": "soccer|a|b|2026-05-25",
            "family": "totals",
            "selection": "Under 2.5",
            "point": 2.5,
            "canonical_ev_pct": -1.2,
            "quality_reasons": ["post_calibration_probability_guard"],
        }]
    })
    write_json(export / "latest-controlled-fallback-report.json", {
        "evaluated": [{
            "match_key": "soccer|a|b|2026-05-25",
            "home_team": "Team A",
            "away_team": "Team B",
            "league_name": "League",
            "family": "totals",
            "selection": "Under 2.5",
            "point": 2.5,
            "metrics": {
                "odds": 2.2,
                "canonical_ev_pct": 3.4,
                "canonical_edge_pp": 1.4,
                "quality_score": 77,
                "confidence": 80,
                "books_count": 3,
            },
            "reject_reasons": ["tier_a_canonical_edge_below_min"],
        }]
    })
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_prediction_calibration_audit.py"
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code in (0, None)
    payload = json.loads((export / "latest-prediction-calibration-audit.json").read_text(encoding="utf-8"))
    row = payload["rows"][0]
    assert row["home_team"] == "Team A"
    assert row["away_team"] == "Team B"
    assert row["odds"] == 2.2
    assert row["quality"] == 77
    assert "post_calibration_probability_guard" in row["reasons"]


def test_prediction_ledger_current_run_missing_metrics_ignores_old_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "run2")
    export = tmp_path / ".data" / "exports"
    ledger = tmp_path / ".data" / "prediction-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "ledger_id": "run1|old",
        "run_id": "run1",
        "status": "rejected_or_watch",
        "candidate_key": "old",
        "reasons": [],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (export / "latest-run-bot.log").parent.mkdir(parents=True, exist_ok=True)
    (export / "latest-run-bot.log").write_text("runtime status 0\n", encoding="utf-8")
    write_json(export / "latest-controlled-fallback-report.json", {
        "candidates_seen": 1,
        "evaluated": [{
            "match_key": "soccer|a|b|2026-05-25",
            "home_team": "Team A",
            "away_team": "Team B",
            "family": "totals",
            "selection": "Under 2.5",
            "point": 2.5,
            "metrics": {"odds": 2.1, "canonical_ev_pct": 4.0, "canonical_edge_pp": 1.9, "quality_score": 72},
            "reject_reasons": ["watch_only"],
        }]
    })
    script = Path(__file__).resolve().parents[1] / "scripts" / "update_prediction_ledger.py"
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code in (0, None)
    summary = json.loads((export / "latest-prediction-ledger-summary.json").read_text(encoding="utf-8"))
    assert summary["new_rows_added"] == 1
    assert summary["current_run_rows"] == 1
    assert summary["rows_missing_core_metrics_current_run"] == 0
    assert summary["rows_missing_core_metrics_total"] == 1


def test_prediction_ledger_does_not_log_nonfatal_runtime_warning_when_fallback_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "run3")
    export = tmp_path / ".data" / "exports"
    export.mkdir(parents=True, exist_ok=True)
    (export / "latest-run-bot.log").write_text(
        "RuntimeError: asyncio.run() cannot be called from a running event loop\nruntime status 0\n",
        encoding="utf-8",
    )
    write_json(export / "latest-controlled-fallback-report.json", {"candidates_seen": 1, "evaluated": []})
    script = Path(__file__).resolve().parents[1] / "scripts" / "update_prediction_ledger.py"
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code in (0, None)
    summary = json.loads((export / "latest-prediction-ledger-summary.json").read_text(encoding="utf-8"))
    assert summary["by_status"].get("runtime_error") is None
