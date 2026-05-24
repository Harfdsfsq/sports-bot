from scripts.send_harizon_telegram_run_report_v9 import filtered_pool_filter_counts


def test_source_pool_counters_are_not_pre_eval_filters():
    counts = {
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality": 4,
        "debug_candidates_before_quality_duplicate_in_pool": 1,
        "latest_rescue_candidates": 4,
    }
    assert filtered_pool_filter_counts(counts) == {}


def test_real_prefilter_reasons_are_kept():
    counts = {
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality": 4,
        "debug_candidates_before_quality_stale_or_outside_window": 2,
        "latest_rescue_candidates_not_in_day_inventory": 1,
        "debug_candidates_before_quality_отрицательная контрольная ценность после пересчёта по выбранному коэффициенту_prefilter": 3,
    }
    assert filtered_pool_filter_counts(counts) == {
        "debug_candidates_before_quality_stale_or_outside_window": 2,
        "latest_rescue_candidates_not_in_day_inventory": 1,
        "debug_candidates_before_quality_отрицательная контрольная ценность после пересчёта по выбранному коэффициенту_prefilter": 3,
    }
