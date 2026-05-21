from __future__ import annotations

from app.services import progressive_upcoming_gap_finalizer as finalizer
from scripts import send_harizon_telegram_run_report_v8 as report_v8


def test_progressive_core_contract_excludes_zero_budget_sportlogic(monkeypatch):
    monkeypatch.setenv("SPORTLOGIC_ENABLED", "false")
    monkeypatch.setenv("SPORTLOGIC_REQUEST_BUDGET_GRANTED", "0")
    monkeypatch.setenv("ODDS_API_IO_REQUEST_BUDGET_GRANTED", "100")
    monkeypatch.setenv("BZZOIRO_REQUEST_BUDGET_GRANTED", "80")
    monkeypatch.setenv("SSTATS_REQUEST_BUDGET_GRANTED", "100")

    assert "sportlogic" not in finalizer._effective_core_odds()
    assert finalizer._effective_core_odds() == {"odds_api_io", "bzzoiro"}
    assert finalizer._effective_core_context() == {"bzzoiro", "sstats"}


def test_v8_report_does_not_force_sportlogic_into_active_core_contract():
    payload = {
        "status": "candidates_failed",
        "status_ru": "🟡 кандидаты есть, quality/value не пропустили",
        "top_reason": "quality bad historical segment guard",
        "coverage": {
            "matches_seen": 408,
            "day_inventory_total": 408,
            "matches_with_offers": 211,
            "matches_with_context": 198,
            "ready_for_model": 32,
            "odds_offers_primary": 27614,
            "bzzoiro_secondary_offers_added": 352,
            "matches_with_2plus_books": 135,
            "bzzoiro_odds_overlap_with_odds_api_io": 13,
            "secondary_combinations": {},
        },
        "funnel": {
            "raw_candidates": 3,
            "candidates_before_quality": 3,
            "passed_candidates": 0,
            "publishable_candidates": 0,
            "published_count": 0,
            "main_pipeline_published": False,
            "main_pipeline_published_count": 0,
            "fallback_status": "no_viable_controlled_fallback",
            "fallback_candidates_seen": 3,
            "fallback_evaluated": 3,
            "fallback_published": False,
            "fallback_published_count": 0,
            "windowed_audit_candidates": 3,
            "windowed_publish_allowed": 2,
            "windowed_publish_blocked": 1,
            "publish_filter_input": 0,
        },
        "api": {
            "odds_api_io": {"events_req": 0, "odds_req": 80, "matched": 398, "offers": 27614, "books_2plus": 135, "errors": 0, "auth_failed": False},
            "sstats": {"requests": 22, "contexts": 194, "rows": 18329, "errors": 0, "deep_enriched": 11},
            "bzzoiro": {"requests": 41, "contexts": 10, "events": 0, "secondary_offers_added": 352, "overlap": 13, "errors": 0},
            "sportlogic": {"enabled": False, "requests": 0, "odds_requests": 0, "matched": 0, "offers": 0, "errors": 0},
        },
        "line_guard": {"final_pre_kickoff_checks": 14, "no_more_regular_run_before_kickoff": 14, "seen": 1, "kept": 1, "dropped": 0},
        "reasons": [],
        "samples": {},
        "diagnostics": {
            "progressive_core_coverage": {
                "contract": {
                    "core_odds_providers": ["odds_api_io", "bzzoiro"],
                    "core_context_providers": ["bzzoiro", "sstats"],
                    "excluded_core_providers": ["sportlogic"],
                },
                "counts": {
                    "matches_tracked": 10,
                    "core_odds_2plus": 1,
                    "core_context_2plus": 3,
                    "core_ready_2plus_both": 1,
                    "window_0_4h": 0,
                    "window_0_12h": 0,
                },
            }
        },
    }

    text = report_v8.render(payload)

    assert "Active core odds/line: bzzoiro,odds_api_io" in text
    assert "Active core odds/line: bzzoiro,odds_api_io,sportlogic" not in text
    assert "Excluded from active core: sportlogic" in text
    assert "Progressive coverage не видит матчей в ближайшие 12 часов" in text
