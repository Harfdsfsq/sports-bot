from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


def load_module(root: Path):
    spec = importlib.util.spec_from_file_location('probe_targeted_secondary_sources', root / 'scripts' / 'probe_targeted_secondary_sources.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def test_target_rows_drop_stale_near_misses_and_fill_from_runtime_topup(tmp_path, monkeypatch):
    root = tmp_path
    src = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_targeted_secondary_sources.py'
    dst = root / 'scripts' / 'probe_targeted_secondary_sources.py'
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.chdir(root)
    monkeypatch.setenv('DAY_INVENTORY_TARGET_DATE', '2026-05-31')
    monkeypatch.setenv('SECONDARY_PROVIDER_PROBE_NOW_UTC', '2026-05-31T19:50:00+00:00')

    write(root / '.data/day_inventory/runtime_topup_roster_2026-05-31.json', {
        'matches': [
            {'match_key': 'soccer|future|team|2026-05-31', 'home_team': 'Future FC', 'away_team': 'Team FC', 'kickoff_utc': '2026-05-31T21:00:00+00:00', 'league_name': 'X'},
            {'match_key': 'soccer|another|row|2026-05-31', 'home_team': 'Another FC', 'away_team': 'Row FC', 'kickoff_utc': '2026-05-31T22:00:00+00:00', 'league_name': 'X'},
        ]
    })
    (root / '.data').mkdir(exist_ok=True)
    (root / '.data/rejected-near-miss-ledger.jsonl').write_text(
        json.dumps({'match_key': 'soccer|old|row|2026-05-30', 'home_team': 'Old FC', 'away_team': 'Row FC', 'commence_time': '2026-05-30T12:00:00+00:00'}) + '\n' +
        json.dumps({'match_key': 'soccer|future|team|2026-05-31', 'home_team': 'Future FC', 'away_team': 'Team FC', 'commence_time': '2026-05-31T21:00:00+00:00'}) + '\n',
        encoding='utf-8',
    )

    mod = load_module(root)
    rows = mod.target_rows(4)
    keys = [mod.target_match_key(r) for r in rows]
    assert 'soccer|old|row|2026-05-30' not in keys
    assert 'soccer|future|team|2026-05-31' in keys
    assert 'soccer|another|row|2026-05-31' in keys


def test_matched_contexts_use_active_inventory_match_key(tmp_path, monkeypatch):
    root = tmp_path
    src = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_targeted_secondary_sources.py'
    dst = root / 'scripts' / 'probe_targeted_secondary_sources.py'
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.chdir(root)
    monkeypatch.setenv('DAY_INVENTORY_TARGET_DATE', '2026-05-31')
    monkeypatch.setenv('SECONDARY_PROVIDER_PROBE_NOW_UTC', '2026-05-31T19:00:00+00:00')
    write(root / '.data/day_inventory/runtime_topup_roster_2026-05-31.json', {
        'matches': [{'match_key': 'soccer|cordoba_cf|sd_huesca|2026-05-31', 'home_team': 'Cordoba CF', 'away_team': 'SD Huesca', 'kickoff_utc': '2026-05-31T21:00:00+00:00'}]
    })
    mod = load_module(root)
    targets = [{'home_team': 'Córdoba', 'away_team': 'Huesca', 'commence_time': '2026-05-31T21:00:00+00:00'}]
    rows = [{'id': 1, 'homeTeam': {'name': 'Cordoba CF'}, 'awayTeam': {'name': 'SD Huesca'}, 'date': '2026-05-31T21:00:00.000Z'}]
    contexts = mod.matched_contexts(rows, targets, 'highlightly')
    assert contexts
    assert contexts[0]['match_key'] == 'soccer|cordoba_cf|sd_huesca|2026-05-31'
