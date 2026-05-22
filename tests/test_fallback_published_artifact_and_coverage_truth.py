from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_truth_marks_already_published_and_minutes(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod = _load_module(Path('/mnt/data/patchbase263020/scripts/build_day_inventory_coverage_truth.py'), 'coverage_truth_mod')
    (tmp_path / '.data/day_inventory').mkdir(parents=True)
    (tmp_path / '.data').mkdir(parents=True, exist_ok=True)
    (tmp_path / '.data/fallback-sent-index.json').write_text(json.dumps({
        'k': {
            'telegram_sent': True,
            'match_key': 'soccer|acf fiorentina|atalanta|2026-05-22',
        }
    }), encoding='utf-8')
    inv = {
        'matches': [{
            'match_key': 'soccer|acf fiorentina|atalanta|2026-05-22',
            'kickoff_utc': '2026-05-22T18:45:00+00:00',
            'home_team': 'ACF Fiorentina',
            'away_team': 'Atalanta BC',
            'coverage': {'odds': True, 'context': True, 'ready_for_model': True},
            'odds_sources': ['odds_api_io', 'bzzoiro'],
            'context_sources': ['sstats', 'bzzoiro'],
            'books': ['Betfair Exchange', 'Unibet'],
        }]
    }
    (tmp_path / '.data/day_inventory/2026-05-22.json').write_text(json.dumps(inv), encoding='utf-8')
    monkeypatch.setenv('DAY_INVENTORY_TARGET_DATE', '2026-05-22')
    monkeypatch.setenv('HARIZON_RUN_NOW_UTC', '2026-05-22T17:39:30+00:00')
    assert mod.main() == 0
    payload = json.loads((tmp_path / '.data/exports/latest-day-inventory-coverage-truth.json').read_text())
    row = payload['rows'][0]
    assert row['strict_ready_for_publish'] is True
    assert row['already_published'] is True
    assert row['ready_for_publish'] is False
    assert row['minutes_to_kickoff'] == 65.5
    assert payload['counts']['matches_ready_for_publish_strict'] == 1
    assert payload['counts']['matches_ready_for_publish'] == 0


def test_fallback_persists_sent_rows_to_standard_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod = _load_module(Path('/mnt/data/patchbase263020/scripts/publish_controlled_fallback.py'), 'fallback_mod')
    row = {
        'dedupe_key': 'abc',
        'match_key': 'soccer|a|b|2026-05-22',
        'home_team': 'A',
        'away_team': 'B',
        'family': 'totals',
        'selection': 'Under',
        'point': 2.5,
        'odds': 2.1,
        'stake': 5.0,
        'stake_amount': 5.0,
        'commence_time': '2026-05-22T20:00:00+00:00',
        'telegram_sent': True,
        'publication_lifecycle_status': 'telegram_sent',
    }
    mod.persist_fallback_publication_rows([row])
    for rel in ['.data/exports/latest-picks.json', '.data/exports/latest-bets.json', '.data/exports/latest-pending-bets.json']:
        rows = json.loads((tmp_path / rel).read_text())
        assert len(rows) == 1
        assert rows[0]['dedupe_key'] == 'abc'
        assert rows[0]['telegram_sent'] is True
    index = json.loads((tmp_path / '.data/published-candidate-index.json').read_text())
    assert index['sent'][0]['match_key'] == 'soccer|a|b|2026-05-22'
