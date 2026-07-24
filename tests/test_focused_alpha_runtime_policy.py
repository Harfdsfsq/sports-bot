from __future__ import annotations

from pathlib import Path

from app.services import focused_alpha_runtime_policy as policy


def test_policy_replaces_volume_and_b_tier_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(policy, "OUT", tmp_path / "policy.json")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED", "5")
    monkeypatch.setenv("PUBLISH_ALLOW_B_TIER", "true")
    monkeypatch.setenv("FOCUSED_ALPHA_LIVE_ENABLED", "true")

    report = policy.apply(force=True)

    assert report["status"] == "applied"
    assert report["publication_minimum_count"] == 0
    assert report["daily_max_published"] == 2
    assert report["effective"]["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"] == "2"
    assert report["effective"]["PUBLISH_ALLOW_B_TIER"] == "false"
    assert report["effective"]["FOCUSED_ALPHA_LIVE_ENABLED"] == "false"
    assert report["effective"]["CONTROLLED_FALLBACK_USE_QUALITY_PROXY"] == "false"
    assert report["publication_contract_relaxed"] is False
