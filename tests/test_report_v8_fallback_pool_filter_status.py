from pathlib import Path


def test_v8_reports_fallback_pool_filters_before_quality_status():
    src = Path("scripts/send_harizon_telegram_run_report_v8.py").read_text(encoding="utf-8")
    assert "def _apply_controlled_fallback_pool_status" in src
    assert "candidates_filtered_before_fallback" in src
    assert "кандидаты отфильтрованы до fallback" in src
    assert "controlled_fallback_pool_filter_counts" in src


def test_v8_renders_day_inventory_membership_filter_block():
    src = Path("scripts/send_harizon_telegram_run_report_v8.py").read_text(encoding="utf-8")
    assert "Controlled fallback pool filter" in src
    assert "day_inventory_membership_keys" in src
    assert "_not_in_day_inventory" in src
    assert "кандидат не входит в frozen day inventory" in src
