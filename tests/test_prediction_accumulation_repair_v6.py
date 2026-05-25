from __future__ import annotations

import json
from pathlib import Path


def test_repair_prediction_accumulation_outputs_removes_api_only_rows(tmp_path, monkeypatch):
    import importlib.util

    script = Path('scripts/repair_prediction_accumulation_outputs.py')
    spec = importlib.util.spec_from_file_location('repair_prediction_accumulation_outputs', script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, 'ROOT', tmp_path)
    monkeypatch.setattr(mod, 'EXPORT_DIR', tmp_path / '.data' / 'exports')
    monkeypatch.setattr(mod, 'LEDGER', tmp_path / '.data' / 'prediction-ledger.jsonl')
    monkeypatch.setattr(mod, 'SUMMARY', tmp_path / '.data' / 'exports' / 'latest-prediction-ledger-summary.json')
    monkeypatch.setattr(mod, 'CALIBRATION', tmp_path / '.data' / 'exports' / 'latest-prediction-calibration-audit.json')
    monkeypatch.setattr(mod, 'REPORT', tmp_path / '.data' / 'exports' / 'latest-prediction-accumulation-repair.json')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')

    mod.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            'ledger_id': '123|coverage-only', 'run_id': '123', 'status': 'rejected_or_watch',
            'stage_seen': ['api_coverage'], 'home_team': 'A', 'away_team': 'B',
            'odds': 2.2, 'ev_pct': 7.0, 'edge_pp': None, 'reasons': [],
        },
        {
            'ledger_id': '123|fallback', 'run_id': '123', 'status': 'rejected_or_watch',
            'stage_seen': ['api_coverage', 'fallback'], 'home_team': 'C', 'away_team': 'D',
            'odds': 2.1, 'ev_pct': 3.0, 'edge_pp': 1.0, 'reasons': ['xg_direction_conflict'],
        },
    ]
    mod.LEDGER.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    mod.write_json(mod.SUMMARY, {'current_run_id': '123'})
    mod.write_json(mod.CALIBRATION, {
        'counts': {'candidate_keys': 2},
        'rows': [
            {'key': 'coverage-only', 'stage_seen': {'api_coverage': True}, 'home_team': 'A', 'away_team': 'B', 'odds': 2.2},
            {'key': 'fallback', 'stage_seen': {'api_coverage': True, 'fallback': True}, 'home_team': 'C', 'away_team': 'D', 'odds': 2.1, 'edge_after_pp': 1.0},
        ],
    })

    assert mod.main() == 0
    kept = [json.loads(line) for line in mod.LEDGER.read_text(encoding='utf-8').splitlines()]
    assert len(kept) == 1
    assert kept[0]['ledger_id'] == '123|fallback'
    summary = json.loads(mod.SUMMARY.read_text(encoding='utf-8'))
    assert summary['current_run_rows'] == 1
    assert summary['rows_missing_core_metrics_current_run'] == 0
    assert summary['coverage_only_rows_removed_current_run'] == 1
    calibration = json.loads(mod.CALIBRATION.read_text(encoding='utf-8'))
    assert calibration['counts']['candidate_keys'] == 1
    assert calibration['counts']['coverage_only_rows_removed'] == 1
