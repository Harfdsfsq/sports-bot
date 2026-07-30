from __future__ import annotations

from pathlib import Path

from app.services import focused_alpha_runtime_contract as contract
from app.services import focused_alpha_runtime_policy as policy


def test_policy_reasserts_rules_ab_tiers_and_separate_windows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(contract, "OUT", tmp_path / "policy.json")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED", "5")
    monkeypatch.setenv("PUBLISH_ALLOW_B_TIER", "true")
    monkeypatch.setenv("FOCUSED_ALPHA_LIVE_ENABLED", "true")

    report = policy.apply(force=True)

    assert report["status"] == "applied"
    assert report["publication_minimum_count"] == 0
    assert report["daily_max_published"] == 2
    assert report["effective"]["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"] == "2"
    assert report["effective"]["PUBLISH_ALLOW_B_TIER"] == "true"
    assert report["effective"]["PUBLISH_COVERAGE_TIER_MODE"] == "hybrid"
    assert report["effective"]["PUBLISH_MIN_ODDS_SOURCES"] == "1"
    assert report["effective"]["PUBLISH_MIN_CONTEXT_SOURCES"] == "1"
    assert report["effective"]["PUBLISH_MIN_BOOKS"] == "2"
    assert report["effective"]["PUBLISH_TIER_A_MIN_ODDS_SOURCES"] == "2"
    assert report["effective"]["PUBLISH_TIER_A_MIN_CONTEXT_SOURCES"] == "2"
    assert report["effective"]["PUBLISH_TIER_B_MIN_ODDS_SOURCES"] == "1"
    assert report["effective"]["PUBLISH_TIER_B_MIN_CONTEXT_SOURCES"] == "1"
    assert report["effective"]["HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW"] == "false"
    assert report["effective"]["PREDICTION_PUBLICATION_ENABLED"] == "true"
    assert report["effective"]["NIGHTLY_REVIEW_REPORT_ONLY_ENABLED"] == "false"
    assert report["effective"]["FOCUSED_ALPHA_LIVE_ENABLED"] == "false"
    assert report["effective"]["CONTROLLED_FALLBACK_USE_QUALITY_PROXY"] == "false"
    assert report["main_publication_disabled_for_wide_data_window"] is False
    assert report["publication_contract_relaxed"] is False
