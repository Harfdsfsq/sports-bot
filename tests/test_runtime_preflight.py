from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.services.runtime_preflight import RuntimePreflight, SAFE_RUNTIME_DEFAULTS, setdefault_env


def test_setdefault_env_does_not_clobber_existing_values(monkeypatch):
    monkeypatch.setenv("MIN_SOURCES_PUBLISH", "3")
    monkeypatch.delenv("PUBLISH_MIN_CONTEXT_SOURCES", raising=False)

    applied = setdefault_env({"MIN_SOURCES_PUBLISH": "1", "PUBLISH_MIN_CONTEXT_SOURCES": "1"})

    assert applied == 1
    assert SAFE_RUNTIME_DEFAULTS["MIN_SOURCES_PUBLISH"] == "1"
    assert __import__("os").getenv("MIN_SOURCES_PUBLISH") == "3"
    assert __import__("os").getenv("PUBLISH_MIN_CONTEXT_SOURCES") == "1"


def test_preflight_defaults_match_rules_b_tier_contract():
    assert SAFE_RUNTIME_DEFAULTS["MIN_BOOKS_PUBLISH"] == "2"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_MIN_ODDS_SOURCES"] == "1"
    assert SAFE_RUNTIME_DEFAULTS["PUBLISH_MIN_CONTEXT_SOURCES"] == "2"
    assert SAFE_RUNTIME_DEFAULTS["CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM"] == "false"
    assert SAFE_RUNTIME_DEFAULTS["CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM"] == "false"


def test_preflight_can_run_without_legacy_extensions_or_discovery(monkeypatch):
    monkeypatch.setenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED", "false")
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED", "false")
    out_dir = Path(".codex_tmp") / f"preflight-test-{uuid4().hex}"

    report = RuntimePreflight(export_dir=out_dir).run_before_prediction(stage="test")

    assert report.discovery_first == {"enabled": False, "reason": "disabled"}
    assert report.legacy_extensions == {"enabled": False, "reason": "disabled"}
    payload = json.loads((out_dir / "latest-runtime-preflight.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "test"
    assert payload["safe_defaults_applied"] >= 0
