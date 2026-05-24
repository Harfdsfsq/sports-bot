from __future__ import annotations

from types import SimpleNamespace

from app.services.candidate_factory_output_dedup_patch import dedupe_candidates
from scripts.send_harizon_telegram_run_report_v9 import is_real_pool_filter, pool_filter_counts


def test_dedup_keeps_best_logical_candidate():
    a = SimpleNamespace(match_key='m1', family='totals', selection='Меньше', point=2.5, ev_pct=5.0, edge_pct=2.0, confidence=70, quality_score=80, publication_score=10)
    b = SimpleNamespace(match_key='m1', family='totals', selection='Меньше', point='2.50', ev_pct=7.0, edge_pct=3.0, confidence=72, quality_score=82, publication_score=11)
    kept, duplicates = dedupe_candidates([a, b])
    assert kept == [b]
    assert len(duplicates) == 1


def test_v9_pool_filter_classifier_excludes_source_counters():
    counts = {
        'day_inventory_membership_keys': 597,
        'debug_candidates_before_quality': 1,
        'debug_candidates_before_quality_duplicate_in_pool': 1,
        'debug_candidates_before_quality_canonical_negative_value_prefilter': 1,
        'artifact_rescue_candidates_stale_or_outside_window': 2,
    }
    assert not is_real_pool_filter('debug_candidates_before_quality')
    assert not is_real_pool_filter('debug_candidates_before_quality_duplicate_in_pool')
    assert pool_filter_counts(counts) == {
        'debug_candidates_before_quality_canonical_negative_value_prefilter': 1,
        'artifact_rescue_candidates_stale_or_outside_window': 2,
    }
