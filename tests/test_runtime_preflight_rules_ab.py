from __future__ import annotations
import os
from app.services.runtime_preflight import RuntimePreflight, SAFE_RUNTIME_DEFAULTS

def test_rules_ab_defaults_match_written_contract():
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_ALLOW_B_TIER"] == "true"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_TIER_A_MIN_ODDS_SOURCES"] == "2"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_TIER_A_MIN_CONTEXT_SOURCES"] == "2"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_TIER_B_MIN_ODDS_SOURCES"] == "1"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_TIER_B_MIN_CONTEXT_SOURCES"] == "1"
    assert SAFE_RUNTIME_DEFAULTS["MIN_BOOKS_PUBLISH"] == "2"
    assert SAFE_RUNTIME_DEFAULTS["FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT"] == "0"

def test_preflight_never_clobbers_workflow_contract(monkeypatch):
    monkeypatch.setenv("PUBLISH_TIER_B_MIN_ODDS_SOURCES", "3")
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED", "false")
    RuntimePreflight().apply_safe_defaults()
    assert os.getenv("PUBLISH_TIER_B_MIN_ODDS_SOURCES") == "3"

def test_focused_alpha_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED", raising=False)
    from app.services.focused_alpha_runtime_policy import apply
    assert apply(force=True)["status"] == "disabled_for_rules_ab"
