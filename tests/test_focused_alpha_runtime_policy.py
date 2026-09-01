from __future__ import annotations

from pathlib import Path

from app.services import focused_alpha_runtime_contract as contract
from app.services import focused_alpha_runtime_policy as policy


def test_focused_alpha_contract_is_opt_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(contract, "OUT", tmp_path / "policy.json")
    monkeypatch.setenv("FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED", "true")
    monkeypatch.setenv("FOCUSED_ALPHA_LIVE_ENABLED", "true")
    report = policy.apply(force=True)
    assert report["status"] == "applied"
    assert report["publication_contract_relaxed"] is False


def test_production_defaults_to_rules_ab(monkeypatch) -> None:
    monkeypatch.setenv("FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED", "false")
    monkeypatch.setenv("FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT", "40")
    report = policy.apply(force=True)
    assert report["status"] == "disabled_for_rules_ab"
    assert report["rules_invariants_applied"] is True
    assert __import__("os").getenv("PUBLISH_ALLOW_B_TIER") == "true"
    assert __import__("os").getenv("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES") == "1"
    assert __import__("os").getenv("FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT") == "0"
