from pathlib import Path


def test_v8_current_inventory_window_counters_are_initialized():
    src = Path("scripts/send_harizon_telegram_run_report_v8.py").read_text(encoding="utf-8")
    assert '"window_0_4h_strict_ready": 0' in src
    assert '"window_0_4h_already_published": 0' in src
    assert '"window_0_12h_strict_ready": 0' in src
    assert '"window_0_12h_already_published": 0' in src


def test_v8_reports_fresh_strict_and_already_sent_truth_counts():
    src = Path("scripts/send_harizon_telegram_run_report_v8.py").read_text(encoding="utf-8")
    assert "ready publish fresh" in src
    assert "matches_ready_for_publish_strict" in src
    assert "matches_strict_ready_already_published" in src
