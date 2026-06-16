from scripts.send_harizon_telegram_run_report_v5 import main_pipeline_sent_count
from scripts.send_harizon_telegram_run_report_v8 import render


def test_main_pipeline_count_ignores_pending_ledger_without_fresh_pick() -> None:
    count, diag = main_pipeline_sent_count(
        summary={},
        publishable=0,
        sent_picks_count=0,
        sent_pending_count=5,
    )

    assert count == 0
    assert diag["ignored_ledger_sent_pending_count"] == 5


def test_main_pipeline_count_blocks_inconsistent_summary_counter() -> None:
    count, diag = main_pipeline_sent_count(
        summary={"published_to_telegram": 5},
        publishable=0,
        sent_picks_count=0,
        sent_pending_count=5,
    )

    assert count == 0
    assert diag["counter_inconsistent"] is True
    assert diag["ignored_summary_published_count"] == 5


def test_main_pipeline_count_accepts_fresh_sent_pick() -> None:
    count, diag = main_pipeline_sent_count(
        summary={},
        publishable=1,
        sent_picks_count=1,
        sent_pending_count=5,
    )

    assert count == 1
    assert diag["ignored_ledger_sent_pending_count"] == 0


def test_v8_render_describes_final_line_guard_drop_without_waiting_snapshot() -> None:
    text = render(
        {
            "status": "not_published",
            "status_ru": "not published",
            "top_reason": "line_movement_guard_dropped",
            "funnel": {"published_count": 0},
            "line_guard": {
                "seen": 1,
                "kept": 0,
                "dropped": 1,
                "waiting_next_run": 0,
                "dropped_final": 1,
            },
            "coverage": {},
            "api": {},
            "diagnostics": {},
            "samples": {},
            "github_actions": {},
        }
    )

    assert "edge/EV/movement" in text
    assert "нужен второй снимок" not in text
