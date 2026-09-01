from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


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


def test_telegram_inventory_shortfall_uses_displayed_inventory_total():
    from scripts import send_harizon_telegram_run_report_v8 as report

    text = report.render(
        {
            "coverage": {"matches_seen": 48},
            "funnel": {},
            "api": {},
            "line_guard": {},
            "diagnostics": {
                "coverage_truth": {"counts": {"matches_total": 173}},
                "inventory_target_expand": {
                    "target": 300,
                    "matches_after": 131,
                    "target_shortfall": 169,
                    "status": "partial_known_rows_only",
                },
            },
        }
    )

    assert "собрано 173/300" in text
    assert "shortfall 127" in text
    assert "target-expand stage 131/300, shortfall 169" in text


def test_telegram_report_marks_failed_runtime_step():
    from scripts import send_harizon_telegram_run_report_v8 as report

    text = report.render(
        {
            "coverage": {"matches_seen": 0},
            "funnel": {},
            "api": {},
            "line_guard": {},
            "diagnostics": {
                "coverage_truth": {"counts": {"matches_total": 10}},
                "run_bot_step_status": {"status": 1},
            },
        }
    )

    assert "Прогнозный прогон не завершился" in text
    assert "runtime failed: run-once завершился status 1" in text


def test_state_json_writer_serializes_datetime(tmp_path):
    from app.state import JsonStateStore

    path = tmp_path / "payload.json"
    JsonStateStore._write_json(
        path,
        {
            "observed_at": datetime(2026, 6, 16, 9, 19, tzinfo=timezone.utc),
            "nested": {"path": Path("x"), "items": {"b", "a"}},
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["observed_at"] == "2026-06-16T09:19:00+00:00"
    assert payload["nested"]["path"] == "x"
    assert payload["nested"]["items"] == ["a", "b"]
