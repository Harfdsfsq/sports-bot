from __future__ import annotations

from pathlib import Path

from app.services import focused_alpha_history as history


def test_history_dedupes_and_recomputes_flat_unit_pnl(tmp_path: Path, monkeypatch) -> None:
    rows = [
        {
            "prediction_id": "same-pick",
            "home_team": "A",
            "away_team": "B",
            "family": "totals",
            "selection_key": "under",
            "point": 2.5,
            "odds": 2.0,
            "status": "pending",
            "telegram_sent": True,
            "_history_source_path": "first.json",
        },
        {
            "prediction_id": "same-pick",
            "home_team": "A",
            "away_team": "B",
            "family": "totals",
            "selection_key": "under",
            "point": 2.5,
            "odds": 2.0,
            "result": "won",
            "settled_at": "2026-07-24T01:00:00+00:00",
            "telegram_sent": True,
            "model_probability_pct": 55.0,
            "context_sources": ["sstats", "bzzoiro"],
            "_history_source_path": "settled.json",
        },
    ]
    monkeypatch.setattr(history, "collect_raw_rows", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(history, "REPORT_PATH", tmp_path / "audit.json")
    monkeypatch.setattr(history, "CANONICAL_PATH", tmp_path / "canonical.json")

    report = history.build_history_audit()

    assert report["canonical_rows"] == 1
    assert report["settled_rows"] == 1
    assert report["duplicates_collapsed"] == 1
    assert report["performance"]["profit_units_flat_1u"] == 1.0
    assert report["performance"]["hit_rate_ex_push_pct"] == 100.0
    assert report["live_learning_ready"] is False
    assert "settled_sample_below_min:1/100" in report["live_learning_blockers"]


def test_league_prior_is_strongly_shrunk_until_learning_ready() -> None:
    report = {
        "live_learning_ready": False,
        "by_league": {
            "League": {
                "settled": 5,
                "yield_pct_flat_1u": 40.0,
            }
        },
    }

    prior = history.league_prior("League", report)

    assert prior["sample"] == 5.0
    assert 0.0 < prior["reliability"] < 1.0
    assert 0.0 < prior["profit_signal"] < 0.05
