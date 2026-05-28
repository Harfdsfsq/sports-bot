from __future__ import annotations

import json
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
