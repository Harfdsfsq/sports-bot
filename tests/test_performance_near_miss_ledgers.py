from __future__ import annotations

import json
from pathlib import Path


def test_performance_and_near_miss_ledgers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.data/exports').mkdir(parents=True)
    report = {
        'published': True,
        'status': 'published',
        'selected': {
            'match_key': 'soccer|a|b|2026-01-01',
            'home_team': 'A',
            'away_team': 'B',
            'selection': 'Under',
            'point': 2.5,
            'metrics': {'odds': 1.91, 'canonical_ev_pct': 5.2, 'canonical_edge_pp': 2.1, 'quality_score': 80, 'odds_sources_count': 1, 'confirmation_sources_count': 2},
            'odds_sources': ['odds_api_io'],
            'confirmation_sources': ['sstats', 'clubelo'],
            'stake': 5,
        },
        'samples': {
            'fallback_evaluated': [
                {
                    'match_key': 'soccer|c|d|2026-01-01',
                    'home_team': 'C',
                    'away_team': 'D',
                    'selection': 'Over',
                    'point': 2.5,
                    'metrics': {'odds': 2.3, 'canonical_ev_pct': 4.0, 'canonical_edge_pp': 1.5, 'quality_score': 74},
                    'reject_reasons': ['tier_b_quality_below_min'],
                }
            ]
        },
    }
    (tmp_path / '.data/exports/latest-controlled-fallback-report.json').write_text(json.dumps(report), encoding='utf-8')
    from scripts import build_performance_and_near_miss_ledgers as mod
    assert mod.main() == 0
    perf_lines = (tmp_path / '.data/performance-ledger.jsonl').read_text(encoding='utf-8').strip().splitlines()
    near_lines = (tmp_path / '.data/rejected-near-miss-ledger.jsonl').read_text(encoding='utf-8').strip().splitlines()
    assert len(perf_lines) == 1
    assert len(near_lines) == 1
    assert json.loads(perf_lines[0])['ledger_type'] == 'published_pick'
    assert json.loads(near_lines[0])['study_bucket'] == 'quality_confidence_gap'


def test_targeted_queue_bzzoiro_prioritizes_second_source_gap():
    from datetime import datetime, timezone, timedelta
    from types import SimpleNamespace
    from app.services import targeted_enrichment_queue as q

    now = datetime.now(timezone.utc)
    m1 = SimpleNamespace(match_key='m1', sport_key='soccer', commence_time=now + timedelta(hours=2), metadata={})
    m2 = SimpleNamespace(match_key='m2', sport_key='soccer', commence_time=now + timedelta(hours=2), metadata={})
    offer = SimpleNamespace(source='odds_api_io', bookmaker='bet365', family='totals')
    selected, info = q.select_for_provider([m1, m2], 'bzzoiro', {'m1': [offer], 'm2': []}, base_limit=1)
    assert selected == [m1]
    assert info['selected_second_odds_source_gap'] == 1
