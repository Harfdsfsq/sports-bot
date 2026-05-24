from __future__ import annotations

from types import SimpleNamespace

from app.services.candidate_factory_output_dedup_patch import _dedupe
from scripts.send_harizon_telegram_run_report_v9 import filtered_pool_filter_counts, _patch_report_conclusion


def test_candidate_factory_output_dedup_keeps_best_logical_candidate():
    a = SimpleNamespace(match_key="m1", family="totals", selection="Under 2.5", point=2.5, ev_pct=1.0, edge_pct=1.0, confidence=70, sources_count=2, books_count=2)
    b = SimpleNamespace(match_key="m1", family="totals", selection="Under 2.5", point="2.50", ev_pct=3.0, edge_pct=2.0, confidence=71, sources_count=2, books_count=3)
    c = SimpleNamespace(match_key="m1", family="totals", selection="Over 2.5", point=2.5, ev_pct=2.0, edge_pct=1.0, confidence=70, sources_count=2, books_count=2)
    rows, dup = _dedupe([a, b, c])
    assert len(rows) == 2
    assert b in rows
    assert c in rows
    assert len(dup) == 1


def test_v9_pool_filter_classifier_keeps_negative_prefilter_not_source_counter():
    counts = filtered_pool_filter_counts({
        "debug_candidates_before_quality": 4,
        "latest_rescue_candidates": 2,
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality_canonical_negative_value_prefilter": 2,
        "debug_candidates_before_quality_duplicate_in_pool": 1,
    })
    assert counts == {"debug_candidates_before_quality_canonical_negative_value_prefilter": 2}


def test_v9_conclusion_names_negative_value_gate():
    payload = {
        "diagnostics": {
            "controlled_fallback_pool_filter_counts": {
                "debug_candidates_before_quality_canonical_negative_value_prefilter": 2
            }
        }
    }
    text = "📌 Вывод\n• Нужно смотреть candidate factory/mapping: линии и контекст есть, но кандидаты не дошли до проверки.\n"
    out = _patch_report_conclusion(text, payload)
    assert "Candidate pipeline работает" in out
    assert "candidate factory/mapping" not in out
