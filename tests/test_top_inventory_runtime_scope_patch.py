from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location(
    'top_inventory_runtime_scope_patch',
    Path(__file__).resolve().parents[1] / 'app' / 'services' / 'top_inventory_runtime_scope_patch.py',
)
scope = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(scope)


def match(home: str, away: str, date: str = '2026-05-30', source: str = 'provider'):
    return SimpleNamespace(
        match_key=f'soccer|{home.lower().replace(" ", "_")}|{away.lower().replace(" ", "_")}|{date}',
        home_team=home,
        away_team=away,
        commence_time=datetime(2026, 5, 30, 10, 0, tzinfo=timezone.utc),
        source=source,
        metadata={'day_inventory': source == 'day_inventory'},
    )


def test_strict_ordered_identity_does_not_match_reversed_team_order():
    ident = scope._identity('FC Alpha', 'Beta Town', '2026-05-30')
    assert ident == 'soccer|fc_alpha|beta_town|2026-05-30'
    reversed_match = SimpleNamespace(
        match_key='soccer|beta_town|fc_alpha|2026-05-30',
        home_team='Beta Town',
        away_team='FC Alpha',
        commence_time=datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc),
    )
    assert scope._match_identity(reversed_match) != ident


def test_filter_matches_uses_direct_or_ordered_identity_and_dedupes():
    inv = match('A FC', 'B Town', source='day_inventory')
    provider_dup = match('A FC', 'B Town', source='provider')
    drop = match('C FC', 'D Town')
    scoped = {
        'direct_keys': {inv.match_key},
        'identities': {scope._match_identity(inv)},
    }
    out = scope._filter_matches([provider_dup, inv, drop], scoped, 300)
    assert out == [inv]


def test_filter_matches_hard_caps_to_max_matches():
    rows = [match(f'Home {i}', f'Away {i}') for i in range(5)]
    scoped = {'direct_keys': {r.match_key for r in rows}, 'identities': {scope._match_identity(r) for r in rows}}
    assert len(scope._filter_matches(rows, scoped, 3)) == 3


def test_prune_progressive_state_to_scope_removes_non_inventory_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, 'PROGRESSIVE_STATE_PATH', tmp_path / 'progressive_coverage_state.json')
    monkeypatch.setattr(scope, 'PROGRESSIVE_EXPORT_PATH', tmp_path / 'latest-progressive-coverage-state.json')
    keep = {
        'match_key': 'soccer|a_fc|b_town|2026-05-30',
        'home_team': 'A FC',
        'away_team': 'B Town',
        'kickoff_utc': '2026-05-30T10:00:00+00:00',
    }
    drop = {
        'match_key': 'soccer|c_fc|d_town|2026-05-30',
        'home_team': 'C FC',
        'away_team': 'D Town',
        'kickoff_utc': '2026-05-30T10:00:00+00:00',
    }
    payload = {'date_local': '2026-05-30', 'matches': {keep['match_key']: keep, drop['match_key']: drop}}
    scope.PROGRESSIVE_STATE_PATH.write_text(__import__('json').dumps(payload), encoding='utf-8')
    scoped = {'direct_keys': {keep['match_key']}, 'identities': {scope._identity('A FC', 'B Town', '2026-05-30')}}
    result = scope._prune_progressive_state_to_scope(scoped, {'date_local': '2026-05-30'}, 'test')
    assert result['progressive_pruned'] == 1
    saved = __import__('json').loads(scope.PROGRESSIVE_STATE_PATH.read_text(encoding='utf-8'))
    assert list(saved['matches']) == [keep['match_key']]
