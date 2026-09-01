from __future__ import annotations

import json
from pathlib import Path

from app.services import focused_alpha_learning_ledger as ledger


def _board(created: str, odds: float) -> dict:
    row = {
        "decision_key": "match|totals|under|2.5",
        "match_key": "match",
        "home_team": "A",
        "away_team": "B",
        "league_name": "League",
        "commence_time": "2026-07-24T12:00:00+00:00",
        "family": "totals",
        "selection": "Меньше 2.5",
        "selection_key": "under",
        "point": 2.5,
        "odds": odds,
        "model_probability": 0.56,
        "market_probability": 0.50,
        "conservative_probability": 0.53,
        "edge_pp": 6.0,
        "ev_pct": 12.0,
        "conservative_ev_pct": 6.0,
        "risk_adjusted_utility": 40.0,
        "confidence": 80.0,
        "quality": 80.0,
        "quality_source": "raw",
        "odds_sources_count": 2,
        "context_sources_count": 2,
        "books_count": 3,
        "hard_xg": True,
        "movement_ok": True,
        "movement_status": "movement_confirmed",
        "blockers": [],
        "passes_shadow_contract": True,
    }
    return {
        "created_at_utc": created,
        "ranked": [row],
        "selected_shadow": [row],
    }


def test_learning_ledger_keeps_snapshots_and_latest_pre_kickoff_price(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(ledger, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "run-1")
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")

    ledger.update_learning_ledger(_board("2026-07-24T10:00:00+00:00", 2.10))
    monkeypatch.setenv("GITHUB_RUN_ID", "run-2")
    result = ledger.update_learning_ledger(_board("2026-07-24T11:55:00+00:00", 2.00))

    decision = result["decisions"]["match|totals|under|2.5"]
    assert result["summary"]["observations"] == 2
    assert decision["snapshots"] == 2
    assert decision["taken_or_shadow_odds"] == 2.10
    assert decision["closing_odds_candidate"] == 2.00
    assert decision["clv_pct"] == 5.0
    persisted = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert persisted["summary"]["unique_decisions"] == 1
