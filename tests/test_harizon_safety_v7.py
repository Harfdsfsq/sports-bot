from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zero_zero_xg_is_not_valid_totals_sanity(monkeypatch):
    mod = load_module('scripts/publish_controlled_fallback.py', 'pcf_safety_v7')
    monkeypatch.setenv('CONTROLLED_FALLBACK_MIN_TOTAL_XG_FOR_SANITY', '0.25')
    cand = {
        'family': 'totals',
        'selection': 'Меньше',
        'point': 3.5,
        'expected_home': 0.0,
        'expected_away': 0.0,
    }
    metrics = mod.xg_sanity_metrics(cand, 0.58)
    assert metrics['enabled'] is False
    assert metrics['reason'] == 'xg_zero_placeholder'


def test_dedupe_key_normalizes_ru_under_and_english_under():
    mod = load_module('scripts/publish_controlled_fallback.py', 'pcf_dedupe_v7')
    a = {
        'canonical_match_id': 'soccer|a|b|2026-06-13',
        'family': 'totals',
        'selection': 'Меньше',
        'point': 3.5,
    }
    b = {
        'canonical_match_id': 'soccer|a|b|2026-06-13',
        'family': 'totals',
        'selection': 'Under',
        'selection_key': 'under',
        'point': '3.50',
    }
    assert mod.dedupe_key(a) == mod.dedupe_key(b)


def test_guarded_same_candidate_normalizes_selection_text():
    mod = load_module('scripts/publish_controlled_fallback_guarded.py', 'pcfg_safety_v7')
    a = {'match_key': 'm1', 'family': 'totals', 'selection': 'Меньше', 'point': 3.5}
    b = {'match_key': 'm1', 'market_family': 'totals', 'selection_key': 'under', 'selection': 'Under', 'point': '3.50'}
    assert mod._same_candidate(a, b) is True
