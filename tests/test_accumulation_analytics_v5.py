from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_line_less_value_row_merges_into_fallback_row(tmp_path, monkeypatch):
    root = tmp_path
    exports = root / ".data" / "exports"
    exports.mkdir(parents=True)
    (root / "scripts").mkdir()

    fallback = {
        "evaluated": [{
            "match_key": "soccer|paderborn 07|vfl wolfsburg|2026-05-25",
            "home_team": "SC Paderborn 07",
            "away_team": "VFL Wolfsburg",
            "family": "totals",
            "selection": "Больше",
            "point": 3.5,
            "odds": 3.22,
            "metrics": {
                "canonical_ev_pct": 49.633,
                "canonical_edge_pp": 15.414,
                "quality_score": 100,
                "confidence": 67.8,
                "books_count": 2,
                "odds_sources_count": 1,
                "confirmation_sources_count": 5,
            },
            "reject_reasons": ["odds_above_global_max"],
        }]
    }
    value_patch = {
        "candidates": [{
            "match_key": "soccer|paderborn 07|vfl wolfsburg|2026-05-25",
            "home_team": "SC Paderborn 07",
            "away_team": "VFL Wolfsburg",
            "family": "totals",
            "selection": "Больше",
            "odds": 3.22,
            "ev_pct": 53.0466,
            "edge_pp": 16.4741,
        }]
    }
    (exports / "latest-controlled-fallback-report.json").write_text(json.dumps(fallback), encoding="utf-8")
    (exports / "latest-candidate-value-runtime-patch.json").write_text(json.dumps(value_patch), encoding="utf-8")

    # Empty optional artifacts
    for name in [
        "latest-candidates-before-quality.json",
        "latest-candidates-after-quality.json",
        "latest-api-coverage-consensus-runtime-patch.json",
        "latest-quality-consensus-safe-relief.json",
        "latest-normalized-publication-payloads.json",
    ]:
        (exports / name).write_text("{}", encoding="utf-8")

    src_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.chdir(root)
    monkeypatch.setenv("GITHUB_RUN_ID", "test-run")

    cal = load_module(src_dir / "build_prediction_calibration_audit.py")
    assert cal.main() == 0
    cal_payload = json.loads((exports / "latest-prediction-calibration-audit.json").read_text(encoding="utf-8"))
    assert cal_payload["counts"]["candidate_keys"] == 1
    assert cal_payload["counts"]["line_less_rows_collapsed"] >= 1
    row = cal_payload["rows"][0]
    assert row["point"] == 3.5
    assert row["ev_before_pct"] == 53.0466
    assert row["ev_after_pct"] == 49.633
    assert row["quality"] == 100.0
    assert row["stage_seen"]["value_patch"] is True
    assert row["stage_seen"]["fallback"] is True

    ledger = load_module(src_dir / "update_prediction_ledger.py")
    assert ledger.main() == 0
    summary = json.loads((exports / "latest-prediction-ledger-summary.json").read_text(encoding="utf-8"))
    assert summary["current_run_rows"] == 1
    assert summary["new_rows_added"] == 1
    assert summary["rows_missing_core_metrics_current_run"] == 0
    lines = (root / ".data" / "prediction-ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    item = json.loads(lines[0])
    assert item["point"] == 3.5
    assert item["ev_pct"] == 49.633
    assert item["quality"] == 100.0
    assert set(item["stage_seen"]) == {"fallback", "value_patch"}
