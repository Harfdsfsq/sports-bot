from scripts.send_harizon_telegram_run_report_v8 import _next_step


def test_next_step_prefers_current_quality_block_over_background_line_waiting() -> None:
    payload = {
        "top_reason": "xg_direction_conflict",
        "funnel": {"published_count": 0},
        "reasons": [{"reason": "xg_direction_conflict", "count": 1}],
        "samples": {"fallback_evaluated": [{"reject_reasons": ["xg_direction_conflict"]}]},
    }

    text = _next_step(payload, {"matches_waiting_line_movement": 197}, {})

    assert "quality/xG/value" in text
    assert "publish/decline" not in text


def test_next_step_waits_when_current_candidate_needs_line_recheck() -> None:
    payload = {
        "funnel": {"published_count": 0},
        "reasons": [{"reason": "line_movement_not_confirmed:awaiting_next_run", "count": 1}],
        "samples": {
            "fallback_evaluated": [
                {"reject_reasons": ["line_movement:needs_next_cron_line_movement_recheck"]}
            ]
        },
    }

    text = _next_step(payload, {"matches_waiting_line_movement": 197}, {})

    assert "publish/decline" in text
