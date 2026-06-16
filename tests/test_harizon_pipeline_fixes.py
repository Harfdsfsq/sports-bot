from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_sportlogic_game_id_prefers_game_id_over_odds_row_id():
    from app.providers.sportlogic_provider import SportLogicProvider

    row = {
        "id": 6631453,  # odds-row id; must not be used for /games/{id}
        "game_id": 41200,
        "market_id": 1,
        "option_name": "Home",
        "odds": "1.18",
        "bookmaker": {"id": 1, "name": "Bet365"},
    }

    assert SportLogicProvider._game_id(row) == "41200"
    assert SportLogicProvider._event_id(row) == "41200"


def test_progressive_coverage_state_resets_when_target_date_changes(tmp_path, monkeypatch):
    from app.services import progressive_coverage_runtime_patch as patch

    state_path = tmp_path / "progressive_coverage_state.json"
    export_path = tmp_path / "latest-progressive-coverage-state.json"
    latest_path = tmp_path / "latest.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "progressive_coverage_v1",
                "date_local": "2026-05-12",
                "matches": {
                    "old": {
                        "match_key": "old",
                        "kickoff_utc": "2026-05-12T18:00:00+00:00",
                        "odds_sources": ["odds_api_io"],
                    }
                },
                "runs": [{"created_at_utc": "2026-05-12T00:00:00+00:00"}],
            }
        ),
        encoding="utf-8",
    )
    latest_path.write_text(json.dumps({"date_local": "2026-05-28", "matches": []}), encoding="utf-8")

    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-28")
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setattr(patch, "STATE_PATH", state_path)
    monkeypatch.setattr(patch, "STATE_EXPORT_PATH", export_path)
    monkeypatch.setattr(patch, "DAY_INV_DIR", tmp_path)
    monkeypatch.setattr(patch, "ARCHIVE_DIR", tmp_path / "archive")

    state = patch._load_state()

    assert state["date_local"] == "2026-05-28"
    assert state["matches"] == {}
    assert "target_date_changed" in state.get("reset_reason", "")
    assert list((tmp_path / "archive").glob("*.json"))


def test_line_guard_ingests_watchlist_diagnostic_snapshots(tmp_path, monkeypatch):
    from scripts import update_day_inventory_priority_and_line_state as line_state

    exports = tmp_path / "exports"
    line_history = tmp_path / "line_history"
    exports.mkdir()
    line_history.mkdir()
    candidate_diag = exports / "latest-candidate-value-runtime-patch.json"
    candidate_diag.write_text(
        json.dumps(
            {
                "sample": [
                    {
                        "match_key": "soccer|a|b|2026-05-28",
                        "kickoff": "2026-05-28T11:00:00+00:00",
                        "family": "totals",
                        "selection": "Under",
                        "point": 2.0,
                        "odds": 2.28,
                        "canonical_ev_pct": 5.8,
                        "canonical_edge_pp": 2.5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(line_state, "EXPORT_DIR", exports)
    monkeypatch.setattr(line_state, "LINE_HISTORY_DIR", line_history)
    monkeypatch.setattr(line_state, "LINE_GUARD_REPORT_PATH", exports / "latest-line-movement-guard-report.json")
    monkeypatch.setattr(line_state, "CANDIDATE_PATHS", [])
    monkeypatch.setattr(line_state, "WATCHLIST_SOURCE_PATHS", [candidate_diag])

    now = line_state.parse_dt("2026-05-28T03:00:00+00:00")
    assert now is not None
    report = line_state.mutate_candidate_files("2026-05-28", now)

    assert report["candidate_files_seen"] == 0
    assert report["watchlist_candidates_seen"] == 1
    assert report["watchlist_snapshots_written"] == 1
    history = json.loads((line_history / "2026-05-28.json").read_text(encoding="utf-8"))
    assert history["lines"]


def test_market_family_guard_keeps_b_tier_candidate_factory_rows(monkeypatch):
    from app.services import market_family_publication_guard as guard

    candidate = {
        "family": "totals",
        "selection": "Under 2.5",
        "source_summary": {"odds_sources": ["odds_api_io"], "odds_sources_count": 1},
        "raw_bucket_offers": [{"source": "odds_api_io", "family": "totals", "point": 2.5, "price": 2.0}],
        "point": 2.5,
    }
    monkeypatch.setenv("PUBLICATION_REQUIRE_MIN_ODDS_SOURCES", "true")
    monkeypatch.setenv("PUBLICATION_MIN_ODDS_SOURCES", "2")

    kept, blocked = guard._filter_candidates([candidate], enforce_min_odds=False)
    assert kept == [candidate]
    assert blocked == []

    kept_publish, blocked_publish = guard._filter_candidates([candidate], enforce_min_odds=True)
    assert kept_publish == []
    assert blocked_publish
    assert "insufficient_publication_odds_sources" in blocked_publish[0]["reason"]


def test_market_family_guard_telegram_odds_text_respects_fallback_env(monkeypatch):
    from app.services import market_family_publication_guard as guard

    text = "🎯 Ставка: Тотал меньше 2.5\nodds sources 1"
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM", "false")
    assert "telegram_insufficient_odds_sources:1<2" not in guard._text_block_reasons(text)

    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM", "true")
    assert "telegram_insufficient_odds_sources:1<2" in guard._text_block_reasons(text)


def test_sportlogic_active_odds_embedded_game_fallback_filters_requested_date(monkeypatch):
    from app.services import sportlogic_query_runtime_guard as guard

    class Provider:
        timeout = 1
        def __init__(self):
            self.calls = 0
        def _budget_left(self):
            return True
        def _headers(self):
            return {}
        def _extract_odds_rows(self, payload):
            return payload.get("data", [])
        def _extract_list(self, payload):
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                return payload["data"]
            return []
        def _fixture_datetime(self, row):
            return guard.datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        def _event_id(self, row):
            return str(row.get("game_id") or row.get("id") or "")
        def _game_id(self, row):
            return str(row.get("game_id") or "")
        async def _get_json(self, client, path, params, stats, preview):
            self.calls += 1
            assert path == "/odds"
            return {
                "data": [
                    {"id": 999, "game_id": 123, "game": {"id": 123, "start_time": "2026-05-28T18:00:00Z"}},
                    {"id": 1000, "game_id": 124, "game": {"id": 124, "start_time": "2026-05-02T18:00:00Z"}},
                ]
            }

    monkeypatch.setenv("SPORTLOGIC_ACTIVE_ODDS_FALLBACK_ENABLED", "true")
    provider = Provider()
    stats = {}
    preview = {}

    import asyncio
    rows = asyncio.run(guard._load_games_from_active_odds(provider, ["2026-05-28"], stats, preview))

    assert [row["id"] for row in rows] == [123]
    assert stats["active_odds_rows_seen"] == 2
    assert stats["active_odds_recovered_fixtures"] == 1


def test_controlled_fallback_guard_prefers_candidate_confirmed_movement(monkeypatch):
    import scripts.publish_controlled_fallback_guarded as guarded

    candidate = {
        "match_key": "soccer|jazz|salpa|2026-05-28",
        "family": "totals",
        "selection": "Меньше 2.5",
        "point": 2.5,
        "line_movement_guard": {
            "passed": True,
            "status": "movement_confirmed",
            "line_movement_lifecycle_status": "movement_confirmed",
            "reasons": [],
        },
        "source_summary": {
            "line_movement_lifecycle_status": "movement_confirmed",
            "publication_lifecycle_status": "movement_ready",
        },
    }
    metrics = {}

    movement = guarded.controlled_line_movement_report_guarded(candidate, metrics)

    assert movement["passed"] is True
    assert movement["status"] == "movement_confirmed"
    assert metrics["line_movement"]["status"] == "movement_confirmed"


def test_controlled_fallback_guard_ignores_stale_windowed_block_when_candidate_movement_confirmed(tmp_path, monkeypatch):
    import scripts.publish_controlled_fallback_guarded as guarded

    monkeypatch.setattr(guarded, "ROOT", tmp_path)
    export_dir = tmp_path / ".data" / "exports"
    export_dir.mkdir(parents=True)
    (export_dir / "latest-windowed-core-publication-filter.json").write_text(
        '{"blocked_sample":[{"match_key":"soccer|jazz|salpa|2026-05-28","family":"totals","selection":"Меньше 2.5","point":2.5,"coverage":{"reject_reasons":["needs_next_cron_line_movement_recheck"],"movement":{"reason":"needs_next_cron_line_movement_recheck"}}}]}',
        encoding="utf-8",
    )
    candidate = {
        "match_key": "soccer|jazz|salpa|2026-05-28",
        "family": "totals",
        "selection": "Меньше 2.5",
        "point": 2.5,
        "line_movement_guard": {"passed": True, "status": "movement_confirmed", "reasons": []},
    }

    assert guarded._windowed_movement_reasons(candidate) == []


def test_controlled_fallback_final_cron_allows_candidate_after_line_recheck(monkeypatch):
    import scripts.publish_controlled_fallback_guarded as guarded

    now = datetime.now(timezone.utc)
    candidate = {
        "match_key": "soccer|jazz|salpa|2026-05-28",
        "family": "totals",
        "selection": "Under 2.5",
        "point": 2.5,
        "commence_time": (now + timedelta(hours=4)).isoformat(),
    }
    monkeypatch.setattr(guarded, "_next_scheduled_run_at", lambda current, interval: current + timedelta(hours=1))
    monkeypatch.setattr(guarded, "_line_state_has_previous_recheck", lambda row, current: True)

    assert guarded._final_cron_recheck_reasons(candidate) == []


def test_controlled_fallback_final_window_allows_last_check_without_prior_snapshot(monkeypatch):
    import scripts.publish_controlled_fallback_guarded as guarded

    now = datetime.now(timezone.utc)
    candidate = {
        "match_key": "soccer|jazz|salpa|2026-05-28",
        "family": "totals",
        "selection": "Under 2.5",
        "point": 2.5,
        "commence_time": (now + timedelta(minutes=45)).isoformat(),
    }
    monkeypatch.setattr(guarded, "_next_scheduled_run_at", lambda current, interval: current + timedelta(hours=2))
    monkeypatch.setattr(guarded, "_line_state_has_previous_recheck", lambda row, current: False)

    assert guarded._final_cron_recheck_reasons(candidate) == []


def test_controlled_fallback_waits_for_next_cron_until_line_recheck(monkeypatch):
    import scripts.publish_controlled_fallback_guarded as guarded

    now = datetime.now(timezone.utc)
    candidate = {
        "match_key": "soccer|jazz|salpa|2026-05-28",
        "family": "totals",
        "selection": "Under 2.5",
        "point": 2.5,
        "commence_time": (now + timedelta(hours=4)).isoformat(),
    }
    monkeypatch.setattr(guarded, "_next_scheduled_run_at", lambda current, interval: current + timedelta(hours=1))
    monkeypatch.setattr(guarded, "_line_state_has_previous_recheck", lambda row, current: False)

    reasons = guarded._final_cron_recheck_reasons(candidate)

    assert "controlled_fallback_next_regular_run_before_kickoff" in reasons
    assert "controlled_fallback_missing_line_recheck" in reasons
