from __future__ import annotations

import json
from pathlib import Path

from scripts.publish_controlled_fallback import candidate_metrics, final_publish_guard_reasons


def write_truth(tmp_path: Path, row: dict) -> None:
    path = tmp_path / "artifacts" / "run-bot" / "latest-day-inventory-coverage-truth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": [row]}, ensure_ascii=False), encoding="utf-8")


def single_line_candidate() -> dict:
    return {
        "match_key": "soccer|hybrid|home|away|2026-05-22",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "league_name": "Test League",
        "family": "totals",
        "selection": "Under",
        "point": 2.5,
        "odds": 2.20,
        "adjusted_probability": 0.50,
        "market_probability": 0.44,
        "confidence": 80.0,
        "books_count": 3,
        "odds_sources_count": 1,
        "confirmation_sources_count": 3,
        "confirmation_sources": ["bzzoiro", "sstats", "clubelo"],
        "expected_home": 1.00,
        "expected_away": 0.80,
        "diagnostics": {"quality": {"quality_score": 82.0, "reasons": []}},
    }


def base_truth(**overrides: object) -> dict:
    row = {
        "match_key": "soccer|hybrid|home|away|2026-05-22",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_utc": "2026-05-22T12:00:00+00:00",
        "price_confirmations": 3,
        "odds_sources_count": 1,
        "context_sources_count": 3,
        "need_price_confirmations": 2,
        "need_odds_sources": 2,
        "need_context_sources": 2,
        "missing": ["independent_odds_sources"],
        "ready_for_publish": False,
    }
    row.update(overrides)
    return row


def enable_policy(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_STRICT_TRUTH_FOR_TELEGRAM", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REJECT_QUALITY_REASONS_FOR_TELEGRAM", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EDGE_PP", "4.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EV_PCT", "7.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONTEXT_SOURCES", "3")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONFIDENCE", "76.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_MIN_QUALITY", "78.0")


def test_tier_b_allows_single_line_source_with_strong_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    enable_policy(monkeypatch)
    write_truth(tmp_path, base_truth())

    candidate = single_line_candidate()
    metrics = candidate_metrics(candidate)
    reasons = final_publish_guard_reasons(candidate, metrics, "уровень B")

    assert not [r for r in reasons if r.startswith("strict_truth_")]
    assert metrics["line_source_mode"] == "single_line_plus_context"
    assert metrics["strict_truth_single_line_context_override"] is True


def test_tier_a_still_blocks_single_line_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    enable_policy(monkeypatch)
    write_truth(tmp_path, base_truth())

    metrics = candidate_metrics(single_line_candidate())
    reasons = final_publish_guard_reasons(single_line_candidate(), metrics, "уровень A")

    assert "strict_truth_missing:independent_odds_sources" in reasons
    assert any(r.startswith("strict_truth_odds_sources_below_min") for r in reasons)


def test_tier_b_blocks_single_line_when_context_is_weak(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    enable_policy(monkeypatch)
    write_truth(tmp_path, base_truth(context_sources_count=2))

    candidate = single_line_candidate()
    candidate["confirmation_sources_count"] = 2
    candidate["confirmation_sources"] = ["bzzoiro", "sstats"]
    metrics = candidate_metrics(candidate)
    reasons = final_publish_guard_reasons(candidate, metrics, "уровень B")

    assert "single_line_context_sources_below_min:2/3" in reasons
    assert "strict_truth_missing:independent_odds_sources" in reasons
