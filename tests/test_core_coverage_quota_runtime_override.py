from __future__ import annotations

import json
import os

import app.services.core_coverage_quota_runtime_override as quota


def test_quota_policy_is_strict_and_per_account(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ODDS_API_IO_KEY", "one")
    monkeypatch.setenv("ODDS_API_IO_KEY_2", "two")
    monkeypatch.setenv("SSTATS_API_KEY", "sstats")
    monkeypatch.setenv("BZZOIRO_API_KEY", "bzz")
    monkeypatch.setenv("SPORTLOGIC_API_KEY", "sl")
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    monkeypatch.setattr(quota, "REPORT_PATH", tmp_path / "quota.json")
    monkeypatch.setattr(quota, "_INSTALLED", False)

    report = quota.install()

    assert report["status"] == "installed"
    assert os.environ["ODDS_API_IO_ACCOUNT1_PER_RUN_MAX"] == "100"
    assert os.environ["ODDS_API_IO_ACCOUNT2_PER_RUN_MAX"] == "100"
    assert os.environ["ODDS_API_IO_MAX_REQUESTS_PER_RUN"] == "200"
    assert os.environ["SPORTLOGIC_MAX_REQUESTS_PER_RUN"] == "30"
    assert os.environ["DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS"] == "40"
    assert os.environ["PUBLISH_TIER_A_MIN_ODDS_SOURCES"] == "2"
    assert os.environ["PUBLISH_TIER_A_MIN_CONTEXT_SOURCES"] == "2"
    assert os.environ["PUBLISH_TIER_B_MIN_ODDS_SOURCES"] == "1"
    assert os.environ["PUBLISH_TIER_B_MIN_CONTEXT_SOURCES"] == "2"
    assert os.environ["API_COVERAGE_MIN_EXACT_BOOKS"] == "2"

    persisted = json.loads((tmp_path / "quota.json").read_text(encoding="utf-8"))
    assert persisted["strict_publication"]["A"]["min_exact_odds_sources"] == 2
    assert persisted["strict_publication"]["B"]["min_exact_odds_sources"] == 1
