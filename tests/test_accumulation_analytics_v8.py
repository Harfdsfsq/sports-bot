import json
import os
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def test_ledger_excludes_api_coverage_only_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', 'run1')
    exports = tmp_path / '.data' / 'exports'
    # real fallback candidate
    write(exports / 'latest-controlled-fallback-report.json', {
        'evaluated': [{
            'home_team': 'A', 'away_team': 'B', 'family': 'totals', 'selection': 'Меньше 2.5', 'point': 2.5,
            'metrics': {'odds': 2.1, 'canonical_ev_pct': 4.2, 'canonical_edge_pp': 2.0, 'quality_score': 80},
            'reject_reasons': ['xg_direction_conflict'],
        }]
    })
    # opportunity-only row; should not go into ledger
    write(exports / 'latest-api-coverage-consensus-runtime-patch.json', {
        'sample': [{
            'home_team': 'C', 'away_team': 'D', 'family': 'totals', 'selection': 'Больше 2.5', 'point': 2.5,
            'odds': 3.2, 'ev_pct': 12.0,
        }]
    })
    try:
        runpy.run_path(str(ROOT / 'scripts' / 'update_prediction_ledger.py'), run_name='__main__')
    except SystemExit as exc:
        assert exc.code == 0
    lines = (tmp_path / '.data' / 'prediction-ledger.jsonl').read_text(encoding='utf-8').splitlines()
    rows = [json.loads(x) for x in lines]
    assert len(rows) == 1
    assert rows[0]['home_team'] == 'A'
    summary = json.loads((exports / 'latest-prediction-ledger-summary.json').read_text(encoding='utf-8'))
    assert summary['rows_missing_core_metrics_current_run'] == 0
    assert summary['accumulation_filter']['excluded_coverage_only_rows'] == 1


def test_calibration_audit_separates_opportunity_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exports = tmp_path / '.data' / 'exports'
    write(exports / 'latest-controlled-fallback-report.json', {'evaluated': [{'home_team': 'A', 'away_team': 'B', 'family': 'totals', 'selection': 'Under 2.5', 'metrics': {'odds': 2.0, 'canonical_ev_pct': 5.0, 'canonical_edge_pp': 2.5, 'quality_score': 77}}]})
    write(exports / 'latest-api-coverage-consensus-runtime-patch.json', {'sample': [{'home_team': 'C', 'away_team': 'D', 'family': 'totals', 'selection': 'Over 2.5', 'odds': 3.1, 'ev_pct': 11.0}]})
    try:
        runpy.run_path(str(ROOT / 'scripts' / 'build_prediction_calibration_audit.py'), run_name='__main__')
    except SystemExit as exc:
        assert exc.code == 0
    audit = json.loads((exports / 'latest-prediction-calibration-audit.json').read_text(encoding='utf-8'))
    assert audit['counts']['candidate_keys'] == 1
    assert audit['counts']['coverage_only_rows_excluded'] == 1
    assert audit['rows'][0]['home_team'] == 'A'
    assert audit['opportunity_only_rows_sample'][0]['home_team'] == 'C'
