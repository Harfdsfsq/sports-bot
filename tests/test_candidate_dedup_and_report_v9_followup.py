from __future__ import annotations

from types import SimpleNamespace

import app.services.candidate_factory_output_dedup_patch as dedup
import scripts.send_harizon_telegram_run_report_v9 as v9


def test_dedup_keeps_single_logical_candidate():
    a = SimpleNamespace(match_key="m1", family="totals", selection="Over 2.5", point=2.5, ev_pct=5, edge_pct=2, confidence=60, publication_score=10)
    b = SimpleNamespace(match_key="m1", family="totals", selection="Over 2.5", point=2.5, ev_pct=7, edge_pct=3, confidence=70, publication_score=12)
    kept, duplicates = dedup.dedupe_candidates([a, b])
    assert kept == [b]
    assert len(duplicates) == 1


def test_v9_pool_classifier_ignores_source_counters():
    pool = {
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality": 3,
        "debug_candidates_before_quality_duplicate_in_pool": 3,
    }
    assert v9.pool_filter_counts(pool) == {}


def test_v9_pool_classifier_keeps_real_prefilters():
    pool = {
        "debug_candidates_before_quality": 3,
        "latest_rescue_candidates_stale_or_outside_window": 2,
        "debug_candidates_before_quality_canonical_negative_value_prefilter": 1,
    }
    out = v9.pool_filter_counts(pool)
    assert out["latest_rescue_candidates_stale_or_outside_window"] == 2
    assert out["debug_candidates_before_quality_canonical_negative_value_prefilter"] == 1
    assert "debug_candidates_before_quality" not in out
