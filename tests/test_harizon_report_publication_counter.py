from scripts.send_harizon_telegram_run_report_v5 import has_non_line_candidate_rejections, main_pipeline_sent_count
from scripts.send_harizon_telegram_run_report_v8 import _best_non_line_reject_reason, _candidate_lines, render


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


def test_non_line_candidate_rejections_block_waiting_line_as_top_reason() -> None:
    assert has_non_line_candidate_rejections(
        [
            {
                "reject_reasons": [
                    "tier_c_watch_only",
                    "missing_total_xg_sanity",
                ],
                "metrics": {},
            }
        ]
    )
    assert not has_non_line_candidate_rejections(
        [
            {
                "reject_reasons": ["needs_next_cron_line_movement_recheck"],
                "metrics": {},
            }
        ]
    )


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


def test_v8_render_does_not_hide_xg_stop_behind_waiting_line_guard() -> None:
    payload = {
        "status": "not_published",
        "status_ru": "not published",
        "top_reason": "line_movement_guard_waiting_next_run",
        "reasons": [
            {"reason": "line_movement_guard_waiting_next_run", "count": 1},
            {"reason": "missing_total_xg_sanity", "count": 2},
        ],
        "funnel": {"published_count": 0},
        "line_guard": {
            "seen": 2,
            "kept": 0,
            "dropped": 0,
            "waiting_next_run": 1,
            "dropped_final": 0,
        },
        "coverage": {},
        "api": {},
        "diagnostics": {},
        "samples": {
            "fallback_evaluated": [
                {
                    "home_team": "Qadsia",
                    "away_team": "Al-Salmiya",
                    "selection": "Under 5.5",
                    "reject_reasons": [
                        "tier_c_watch_only",
                        "missing_total_xg_sanity",
                    ],
                    "metrics": {},
                }
            ]
        },
        "github_actions": {},
    }

    assert _best_non_line_reject_reason(payload) == "missing_total_xg_sanity"
    text = render(payload)

    assert "missing total xg sanity" in text
    assert "bookmaker-contract" not in text


def test_v8_candidate_lines_show_proxy_single_source_thresholds() -> None:
    lines = _candidate_lines(
        {
            "samples": {
                "fallback_evaluated": [
                    {
                        "home_team": "Fortaleza",
                        "away_team": "America FC",
                        "selection": "Under 2.5",
                        "reject_reasons": ["proxy_single_source_ev_below_min"],
                        "metrics": {
                            "odds": 1.96,
                            "canonical_ev_pct": 9.388,
                            "canonical_edge_pp": 4.79,
                            "confidence": 70.925,
                            "quality_score": 76.0,
                            "proxy_single_source_thresholds": {
                                "applies": True,
                                "min_edge_pp": 8.0,
                                "min_ev_pct": 15.0,
                                "min_confidence": 78.0,
                            },
                            "xg_sanity": {"enabled": False, "reason": "missing_xg"},
                        },
                    }
                ]
            }
        }
    )

    text = "\n".join(lines)
    assert "fact edge 4.8pp / EV 9.4% / conf 70.9" in text
    assert "min edge 8.0pp / EV 15.0% / conf 78.0" in text
    assert "xG sanity: missing (missing_xg)" in text
