from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest


def test_ab_tier_contract_enables_controlled_b_publication(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path("scripts/apply_ab_tier_bookmaker_contract.py", run_name="__main__")
    assert exc.value.code == 0
    exported = env_file.read_text(encoding="utf-8")
    assert "PUBLISH_ALLOW_B_TIER=true" in exported
    assert "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B=true" in exported
    assert "CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED=true" in exported
    assert "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS=2" in exported
    assert "PUBLISH_TIER_A_MIN_ODDS_SOURCES=2" in exported
    assert "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES=true" in exported
    assert "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES=2" in exported
    assert "PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW=false" in exported
    assert "CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN=3" in exported
    assert "CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH=1" in exported
    assert "CONTROLLED_FALLBACK_EXTRA_PICK_STRICT=true" in exported
    payload = json.loads(Path(".data/exports/latest-ab-tier-bookmaker-contract-policy.json").read_text(encoding="utf-8"))
    assert payload["contract"]["A"]["min_odds_sources"] == 2
    assert payload["contract"]["B"]["mode"] == "controlled_public_fallback"
    assert payload["contract"]["top_bundle"]["max_picks_per_run"] == 3
    assert payload["contract"]["top_bundle"]["max_picks_per_match"] == 1
    assert "value" in payload["contract"]["guards_unchanged"]
    assert "line_movement" in payload["contract"]["guards_unchanged"]
