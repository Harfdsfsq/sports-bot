from scripts.send_harizon_telegram_run_report_v9 import is_real_pool_filter, pool_filter_counts, _strip_controlled_pool_block


def test_source_pool_counter_is_not_filter():
    counts = {
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality": 1,
        "debug_candidates_before_quality_duplicate_in_pool": 1,
        "artifact_rescue_candidates_stale_or_outside_window": 1,
    }
    filters = pool_filter_counts(counts)
    assert "debug_candidates_before_quality" not in filters
    assert "artifact_rescue_candidates_stale_or_outside_window" in filters


def test_strip_pool_block_when_fallback_evaluated():
    text = "A\n\n🧯 Controlled fallback pool filter\n• Pre-evaluation filters: debug candidates before quality: 1\n• Смысл: wrong\n\n📌 Вывод\nB"
    stripped = _strip_controlled_pool_block(text)
    assert "Controlled fallback pool filter" not in stripped
    assert "📌 Вывод" in stripped
