from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from scripts.publish_controlled_fallback import candidate_metrics, final_publish_guard_reasons


def isolated_workspace(monkeypatch, name: str) -> Path:
    base = Path.cwd() / ".codex_tmp" / f"{name}-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(base)
    return base


def write_truth(base: Path, row: dict) -> None:
    path = base / "artifacts" / "run-bot" / "latest-day-inventory-coverage-truth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": [row]}, ensure_ascii=False), encoding="utf-8")


def base_candidate() -> dict:
    return {
        "match_key": "soccer|caracas|racing avellaneda|2026-05-22",
        "home_team": "Racing Club Avellaneda",
        "away_team": "Caracas FC",
        "league_name": "International Clubs - Copa Sudamericana",
        "family": "totals",
        "selection": "Under",
        "point": 2.5,
        "odds": 2.38,
        "adjusted_probability": 0.462,
        "market_probability": 0.401,
        "confidence": 82.0,
        "books_count": 5,
        "odds_sources_count": 3,
        "confirmation_sources_count": 4,
        "confirmation_sources": ["bzzoiro", "clubelo", "espn", "sstats"],
        "expected_home": 1.58,
        "expected_away": 1.06,
        "diagnostics": {"quality": {"quality_score": 87.2, "reasons": ["bad_historical_segment_guard"]}},
    }


def test_controlled_fallback_blocks_candidate_not_ready_by_strict_truth(monkeypatch):
    workspace = isolated_workspace(monkeypatch, "strict-truth-not-ready")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_STRICT_TRUTH_FOR_TELEGRAM", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REJECT_QUALITY_REASONS_FOR_TELEGRAM", "false")
    write_truth(workspace, {
        "match_key": "soccer|caracas|racing avellaneda|2026-05-22",
        "home_team": "Racing Club Avellaneda",
        "away_team": "Caracas FC",
        "kickoff_utc": "2026-05-22T00:00:00+00:00",
        "price_confirmations": 5,
        "odds_sources_count": 0,
        "context_sources_count": 5,
        "need_price_confirmations": 2,
        "need_odds_sources": 2,
        "need_context_sources": 2,
        "missing": ["independent_odds_sources"],
        "ready_for_publish": False,
    })
    metrics = candidate_metrics(base_candidate())
    reasons = final_publish_guard_reasons(base_candidate(), metrics, "уровень A")
    assert "strict_truth_odds_sources_below_min:0/2" in reasons
    assert "strict_truth_missing:independent_odds_sources" in reasons


def test_controlled_fallback_blocks_quality_stop_by_default(monkeypatch):
    workspace = isolated_workspace(monkeypatch, "strict-truth-quality-stop")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_STRICT_TRUTH_FOR_TELEGRAM", "true")
    monkeypatch.delenv("CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS", raising=False)
    write_truth(workspace, {
        "match_key": "soccer|caracas|racing avellaneda|2026-05-22",
        "home_team": "Racing Club Avellaneda",
        "away_team": "Caracas FC",
        "kickoff_utc": "2026-05-22T00:00:00+00:00",
        "price_confirmations": 5,
        "odds_sources_count": 2,
        "context_sources_count": 5,
        "need_price_confirmations": 2,
        "need_odds_sources": 2,
        "need_context_sources": 2,
        "missing": [],
        "ready_for_publish": True,
    })
    metrics = candidate_metrics(base_candidate())
    reasons = final_publish_guard_reasons(base_candidate(), metrics, "уровень A")
    assert "telegram_quality_stop_not_allowed:bad_historical_segment_guard" in reasons
