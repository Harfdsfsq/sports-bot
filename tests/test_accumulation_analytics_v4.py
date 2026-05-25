from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path
import pytest


def write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def copy_scripts(tmp_path: Path, repo_root: Path) -> None:
    dst = tmp_path / 'scripts'
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_root / 'scripts' / 'build_prediction_calibration_audit.py', dst / 'build_prediction_calibration_audit.py')
    shutil.copy(repo_root / 'scripts' / 'update_prediction_ledger.py', dst / 'update_prediction_ledger.py')


def test_quality_is_optional_for_sparse_nonfallback_rows(tmp_path, monkeypatch):
    repo_root = Path.cwd()
    copy_scripts(tmp_path, repo_root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', 'quality-optional')
    exp = tmp_path / '.data' / 'exports'

    write(exp / 'latest-candidate-value-runtime-patch.json', {
        'sample': [{
            'match_key': 'soccer|rfs|tukums|2026-05-25',
            'home': 'FC RFS',
            'away': 'FK Tukums',
            'family': 'totals',
            'selection': 'Меньше 3.5',
            'odds': 2.14,
            'canonical_ev_pct': 7.08,
            'canonical_edge_pp': 3.31,
        }]
    })
    write(exp / 'latest-api-coverage-consensus-runtime-patch.json', {
        'sample': [{
            'match_key': 'soccer|rfs|tukums|2026-05-25',
            'home': 'FC RFS',
            'away': 'FK Tukums',
            'family': 'totals',
            'selection': 'Меньше 3.5',
            'point': 3.5,
            'odds': 2.14,
            'ev_pct': 7.08,
            'edge_pp': 3.31,
            'exact_odds_sources_count': 2,
            'exact_books_count': 2,
            'reject_reasons': ['post_calibration_probability_guard'],
        }]
    })

    with pytest.raises(SystemExit) as exc:
        runpy.run_path('scripts/build_prediction_calibration_audit.py', run_name='__main__')
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        runpy.run_path('scripts/update_prediction_ledger.py', run_name='__main__')
    assert exc.value.code == 0

    audit = json.loads((exp / 'latest-prediction-calibration-audit.json').read_text(encoding='utf-8'))
    summary = json.loads((exp / 'latest-prediction-ledger-summary.json').read_text(encoding='utf-8'))
    ledger_line = (tmp_path / '.data' / 'prediction-ledger.jsonl').read_text(encoding='utf-8').splitlines()[0]
    row = json.loads(ledger_line)

    assert audit['counts']['rows_missing_core_metrics'] == 0
    assert summary['rows_missing_core_metrics_current_run'] == 0
    assert row['home_team'] == 'FC RFS'
    assert row['away_team'] == 'FK Tukums'
    assert row['odds'] == 2.14
    assert row['ev_pct'] == 7.08
    assert row['edge_pp'] == 3.31
    assert row['quality'] is None
