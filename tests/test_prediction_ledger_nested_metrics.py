from __future__ import annotations

import json
import runpy
from pathlib import Path


def test_update_prediction_ledger_handles_nested_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exp = Path('.data/exports')
    exp.mkdir(parents=True)
    payload = {
        'evaluated': [{
            'match_key': 'soccer|a|b|2026-05-25',
            'home_team': 'A',
            'away_team': 'B',
            'family': 'totals',
            'selection': 'Меньше 2.5',
            'point': 2.5,
            'reject_reasons': ['xg_direction_conflict'],
            'metrics': {
                'odds': 2.33,
                'canonical_ev_pct': 3.34,
                'canonical_edge_pp': 1.43,
                'confidence': 74.3,
                'quality_score': 39.1,
                'odds_sources_count': 1,
                'confirmation_sources_count': 1,
                'books_count': 3,
            },
        }]
    }
    (exp / 'latest-controlled-fallback-report.json').write_text(json.dumps(payload), encoding='utf-8')
    runpy.run_path(str(Path(__file__).parents[1] / 'scripts' / 'update_prediction_ledger.py'), run_name='__main__')
    summary = json.loads((exp / 'latest-prediction-ledger-summary.json').read_text(encoding='utf-8'))
    assert summary['new_rows_added'] == 1
    row = json.loads(Path('.data/prediction-ledger.jsonl').read_text(encoding='utf-8').splitlines()[0])
    assert row['ev_pct'] == 3.34
    assert row['edge_pp'] == 1.43
    assert row['quality'] == 39.1


def test_calibration_audit_handles_nested_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exp = Path('.data/exports')
    exp.mkdir(parents=True)
    payload = {
        'evaluated': [{
            'match_key': 'soccer|a|b|2026-05-25',
            'home_team': 'A',
            'away_team': 'B',
            'family': 'totals',
            'selection': 'Меньше 2.5',
            'point': 2.5,
            'reject_reasons': ['xg_direction_conflict'],
            'metrics': {'canonical_ev_pct': 3.34, 'canonical_edge_pp': 1.43, 'quality_score': 39.1, 'odds': 2.33},
        }]
    }
    (exp / 'latest-controlled-fallback-report.json').write_text(json.dumps(payload), encoding='utf-8')
    runpy.run_path(str(Path(__file__).parents[1] / 'scripts' / 'build_prediction_calibration_audit.py'), run_name='__main__')
    audit = json.loads((exp / 'latest-prediction-calibration-audit.json').read_text(encoding='utf-8'))
    assert audit['counts']['fallback'] == 1
    assert audit['rows'][0]['home_team'] == 'A'
    assert audit['rows'][0]['ev_after_pct'] == 3.34
