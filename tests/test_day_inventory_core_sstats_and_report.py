from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone


def test_core_inventory_wires_sstats_as_fixture_context_source():
    from scripts import build_day_inventory_core as core

    assert "sstats" in core.CORE_PROVIDERS

    kickoff = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
    odds = core.make_match(
        source="odds_api_io",
        source_event_id="odds-1",
        league_name="Brazil - Serie B",
        home_team="Botafogo",
        away_team="Ferroviario",
        commence_time=kickoff,
        metadata={"has_current_odds_provider": True},
    )
    sstats = core.make_match(
        source="sstats",
        source_event_id="sstats-1",
        league_name="Brazil - Serie B",
        home_team="Botafogo",
        away_team="Ferroviario",
        commence_time=kickoff,
        metadata={"sstats_has_context_hint": True},
    )

    merged, _crosswalk = core.merge_matches(
        {"odds_api_io": [odds], "bzzoiro": [], "sstats": [sstats], "sportlogic": []},
        object(),
    )

    assert len(merged) == 1
    sources = set(str(merged[0].metadata.get("sources_seen") or "").split(","))
    assert {"odds_api_io", "sstats"} <= sources

    payload = core.enrich_payload_coverage({"matches": [asdict(merged[0])], "counts": {}})
    counts = payload["counts"]
    assert counts["matches_with_odds"] == 1
    assert counts["matches_with_context"] == 1
    assert counts["matches_ready_for_model"] == 1


def test_sstats_row_to_match_accepts_documented_games_list_shape():
    from scripts import build_day_inventory_core as core

    row = {
        "Id": 12345,
        "Home": "Belgium",
        "Away": "Egypt",
        "LeagueName": "FIFA World Cup",
        "DateTime": "2026-06-15T19:00:00+03:00",
    }

    match = core.sstats_row_to_match(row, object())

    assert match is not None
    assert match.source == "sstats"
    assert match.source_event_id == "12345"
    assert match.home_team == "Belgium"
    assert match.away_team == "Egypt"
    assert match.metadata["sstats_has_context_hint"] is True


def test_telegram_inventory_line_reports_target_and_shortfall():
    from scripts import send_harizon_telegram_run_report_v8 as report

    text = report.render(
        {
            "coverage": {"matches_seen": 0},
            "funnel": {},
            "api": {},
            "line_guard": {},
            "diagnostics": {
                "coverage_truth": {"counts": {"matches_total": 98}},
                "inventory_target_expand": {
                    "target": 300,
                    "target_shortfall": 202,
                    "status": "partial_known_rows_only",
                },
            },
        }
    )

    assert "Инвентарь дня: собрано 98/300 матчей" in text
    assert "shortfall 202" in text
    assert "partial_known_rows_only" in text
