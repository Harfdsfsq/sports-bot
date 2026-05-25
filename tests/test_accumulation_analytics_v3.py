from __future__ import annotations

import json
import os
import runpy
from pathlib import Path


def write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def test_ledger_and_calibration_merge_sparse_value_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', 'test-run')
    exp = tmp_path / '.data' / 'exports'
    write(exp / 'latest-candidate-value-runtime-patch.json', {
        'sample': [{
            'match_key': 'soccer|home|away|2026-05-25',
            'home': 'Home FC',
            'away': 'Away FC',
            'family': 'totals',
            'selection': 'Меньше 2.5',
            'odds': 2.33,
            'canonical_ev_pct': 9.37,
            'canonical_edge_pp': 4.02,
        }]
    })
    write(exp / 'latest-controlled-fallback-report.json', {
        'evaluated': [{
            'match_key': 'soccer|home|away|2026-05-25',
            'home_team': 'Home FC',
            'away_team': 'Away FC',
            'family': 'totals',
            'selection': 'Меньше 2.5',
            'point': 2.5,
            'reject_reasons': ['xg_direction_conflict'],
            'metrics': {
                'odds': 2.33,
                'canonical_ev_pct': 3.34,
                'canonical_edge_pp': 1.43,
                'quality_score': 39.1,
                'confidence': 74.3,
                'books_count': 3,
                'odds_sources_count': 2,
                'confirmation_sources_count': 1,
            }
        }]
    })
    for name in [
        'latest-candidates-before-quality.json',
        'latest-candidates-after-quality.json',
        'latest-api-coverage-consensus-runtime-patch.json',
        'latest-quality-consensus-safe-relief.json',
        'latest-normalized-publication-payloads.json',
    ]:
        write(exp / name, {})

    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'build_prediction_calibration_audit.py'), run_name='__main__')
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'update_prediction_ledger.py'), run_name='__main__')

    cal = json.loads((exp / 'latest-prediction-calibration-audit.json').read_text(encoding='utf-8'))
    assert cal['counts']['candidate_keys'] == 1
    row = cal['rows'][0]
    assert row['home_team'] == 'Home FC'
    assert row['odds'] == 2.33
    assert row['ev_before_pct'] == 9.37
    assert row['ev_after_pct'] == 3.34
    assert row['quality'] == 39.1

    summary = json.loads((exp / 'latest-prediction-ledger-summary.json').read_text(encoding='utf-8'))
    assert summary['current_run_rows'] == 1
    assert summary['rows_missing_core_metrics_current_run'] == 0
