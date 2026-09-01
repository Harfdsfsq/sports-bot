from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import focused_alpha_accumulation_runtime_patch as patch


def _row(key: str, match: str, league: str, kickoff: datetime, *, blockers=None) -> dict:
    return {
        "decision_key": key,
        "match_key": match,
        "home_team": f"Home {key}",
        "away_team": f"Away {key}",
        "league_name": league,
        "commence_time": kickoff.isoformat(),
        "family": "totals",
        "selection": "Больше",
        "selection_key": "over",
        "point": 2.5,
        "odds": 1.9,
        "model_probability": 0.56,
        "market_probability": 0.53,
        "conservative_probability": 0.53,
        "edge_pp": 3.4,
        "ev_pct": 6.4,
        "conservative_ev_pct": 0.0,
        "risk_adjusted_utility": 30.0,
        "confidence": 72.0,
        "quality": 80.0,
        "books_count": 3,
        "odds_sources_count": 1,
        "context_sources_count": 1,
        "hard_xg": False,
        "movement_ok": True,
        "blockers": blockers or ["hard_xg_missing", "odds_sources_below_2"],
        "raw_decision_snapshot": {
            "diagnostics": {
                "focused_alpha_evidence_truth": {
                    "odds_sources": ["odds_api_io"],
                    "context_sources": ["sstats"],
                    "bookmakers": ["bet365", "unibet", "betfair_exchange"],
                    "xg_sources": ["market_implied_total_xg"],
                }
            }
        },
    }


def test_accumulation_tracks_two_near_misses_without_publish_rights(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(patch, "ACCUMULATION_PATH", tmp_path / "accumulation.json")
    monkeypatch.setattr(patch, "_persist_shadow", lambda rows: {"status": "ok", "added": len(rows)})
    monkeypatch.setattr(patch, "_runtime_shadow_rows", lambda: ([], {"status": "ok"}))
    monkeypatch.setenv("HARIZON_RUN_ID", "run-1")
    monkeypatch.setenv("FOCUSED_ALPHA_ACCUMULATION_DAILY_MAX", "2")
    now = datetime.now(UTC)
    board = {
        "created_at_utc": now.isoformat(),
        "selected_shadow": [],
        "ranked": [
            _row("a", "match-a", "league-a", now + timedelta(hours=2)),
            _row(
                "conflict",
                "match-conflict",
                "league-conflict",
                now + timedelta(hours=3),
                blockers=["xg_direction_conflict"],
            ),
            _row("b", "match-b", "league-b", now + timedelta(hours=4)),
        ],
    }

    result = patch.accumulate(board)

    assert result["selected_this_run"] == 2
    assert set(result["selected_keys_this_run"]) == {"a", "b"}
    assert result["runtime_shadow_persistence"]["added"] == 2
    assert result["rejection_counts"]["xg_direction_conflict"] == 1
    assert result["telegram_publication_enabled"] is False
    assert result["publication_contract_relaxed"] is False
    assert result["selections"]["a"]["odds_sources"] == ["odds api io"]
    assert result["selections"]["a"]["odds"] == 1.9


def test_accumulation_daily_cap_and_runtime_shadow_settlement(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(patch, "ACCUMULATION_PATH", tmp_path / "accumulation.json")
    monkeypatch.setattr(patch, "_persist_shadow", lambda rows: {"status": "ok", "added": len(rows)})
    now = datetime.now(UTC)
    board = {
        "created_at_utc": now.isoformat(),
        "selected_shadow": [],
        "ranked": [_row("a", "match-a", "league-a", now + timedelta(hours=2))],
    }
    monkeypatch.setenv("HARIZON_RUN_ID", "run-1")
    monkeypatch.setattr(patch, "_runtime_shadow_rows", lambda: ([], {"status": "ok"}))
    first = patch.accumulate(board)
    assert first["selected_this_run"] == 1

    settled = {
        "status": "won",
        "odds": 1.9,
        "tracking_reason": "focused_alpha_accumulation",
        "source_summary": {
            "tracking_reason": "focused_alpha_accumulation",
            "focused_alpha_decision_key": "a",
        },
        "settlement": {"outcome": "won", "final_score": "2:1"},
    }
    monkeypatch.setenv("HARIZON_RUN_ID", "run-2")
    monkeypatch.setattr(
        patch,
        "_runtime_shadow_rows",
        lambda: ([settled], {"status": "ok", "settled_focused_shadow_rows": 1}),
    )
    board["created_at_utc"] = (now + timedelta(minutes=15)).isoformat()
    second = patch.accumulate(board)

    assert second["selected_this_run"] == 0
    assert second["selections"]["a"]["settled"] is True
    assert second["selections"]["a"]["result"] == "won"
    assert second["selections"]["a"]["flat_unit_pnl"] == 0.9
    assert second["summary"]["settled"] == 1
    assert second["summary"]["profit_units_flat_1u"] == 0.9
    assert second["summary"]["hit_rate_ex_push_pct"] == 100.0
    assert second["observations"][-1]["decision_key"] == "a"
