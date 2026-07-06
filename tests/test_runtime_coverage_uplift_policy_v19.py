from __future__ import annotations

from pathlib import Path


def test_coverage_uplift_does_not_disable_sportlogic() -> None:
    text = Path("scripts/apply_coverage_uplift_runtime_policy.py").read_text(encoding="utf-8")

    assert 'SPORTLOGIC_ENABLED": "false"' not in text
    assert 'ENABLE_SPORTLOGIC": "false"' not in text
    assert 'put_limit(env, "SPORTLOGIC", 0)' not in text
    assert "SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED" in text
    assert "v19-sportlogic-enabled-coverage-uplift" in text


def test_inventory_bookmaker_backfill_repairs_context_and_source_counts() -> None:
    text = Path("scripts/backfill_inventory_bookmaker_coverage.py").read_text(encoding="utf-8")

    assert "normalize_bookmaker_name" in text
    assert "context_sources_count" in text
    assert "odds_sources_count" in text
    assert "fuzzy_group" in text
    assert "runtime_offer_context_artifacts_v2" in text
