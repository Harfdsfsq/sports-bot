from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas import CandidateBet
from app.services.harizon_unified_scheme_runtime import _candidate_passes_unified_contract, _finalize_inventory_payload, _line_movement_decision, apply_env_defaults

UTC = timezone.utc


def _candidate(**overrides):
    base = dict(
        match_key='soccer:home:away', sport_key='soccer', league_name='Test League', home_team='Home', away_team='Away',
        commence_time=datetime.now(UTC) + timedelta(minutes=90), family='totals', selection='Over 2.5', selection_key='over_2_5',
        odds=1.95, fair_odds=1.8, implied_probability=51.0, market_probability=50.0, consensus_probability=51.0,
        model_probability=57.0, final_probability=57.0, adjusted_probability=56.0, edge_pct=4.0, ev_pct=5.0,
        confidence=73.0, books_count=2, sources_count=2,
        source_summary={'odds_sources': ['odds_api_io', 'bzzoiro'], 'context_sources': ['sstats', 'bzzoiro'], 'bookmakers': ['bet365', 'unibet']},
        stake_amount=1.0,
    )
    base.update(overrides)
    return CandidateBet(**base)


def test_env_defaults_include_unified_contract(monkeypatch):
    monkeypatch.delenv('HARIZON_UNIFIED_SCHEME_ENABLED', raising=False)
    assert apply_env_defaults() >= 1


def test_candidate_inside_next_run_window_can_publish():
    passed, report = _candidate_passes_unified_contract(_candidate(), now_utc=datetime.now(UTC))
    assert passed is True
    assert report['line_movement']['reason'] == 'inside_next_run_window_publish_now'


def test_candidate_outside_next_window_waits_for_movement():
    cand = _candidate(commence_time=datetime.now(UTC) + timedelta(hours=5))
    decision = _line_movement_decision(cand, now_utc=datetime.now(UTC))
    assert decision['passed'] is False
    assert decision['reason'] == 'hold_for_next_run_line_movement'


def test_contract_blocks_context_source_gap():
    cand = _candidate(source_summary={'odds_sources': ['odds_api_io', 'bzzoiro'], 'context_sources': ['sstats'], 'bookmakers': ['bet365', 'unibet']})
    passed, report = _candidate_passes_unified_contract(cand, now_utc=datetime.now(UTC))
    assert passed is False
    assert 'context_sources_lt_2' in report['reasons']


def test_inventory_is_capped_and_annotated():
    payload = {'counts': {}, 'matches': [{'match_key': f'm{i}', 'canonical_match_id': f'm{i}', 'kickoff_utc': '2026-05-24T10:00:00+00:00', 'priority': i, 'coverage': {}} for i in range(305)]}
    out = _finalize_inventory_payload(payload)
    assert len(out['matches']) == 300
    assert out['counts']['harizon_inventory_target'] == 300
    assert 'harizon_contract' in out['matches'][0]
