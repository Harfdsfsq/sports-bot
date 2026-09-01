from __future__ import annotations

from app.services import authoritative_coverage_planner_patch as patch


def test_verified_metadata_is_merged_without_proxy_sources(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_OBSERVED",
        lambda row, ledger: (["odds_api_io"], ["sstats"]),
    )
    row = {
        "metadata": {
            "verified_odds_sources": ["odds_api_io", "bzzoiro"],
            "verified_context_sources": ["sstats", "clubelo"],
        },
        "coverage": {
            "daily_coverage_evidence_synced": True,
            "odds_sources": ["odds_api_io", "bzzoiro"],
            "context_sources": ["sstats", "clubelo"],
        },
    }
    odds, contexts = patch._observed(row, {})
    assert set(odds) == {"odds_api_io", "bzzoiro"}
    assert set(contexts) == {"sstats", "clubelo"}


def test_unsynced_coverage_lists_are_not_promoted(monkeypatch):
    monkeypatch.setattr(patch, "_ORIGINAL_OBSERVED", lambda row, ledger: ([], []))
    row = {
        "coverage": {
            "daily_coverage_evidence_synced": False,
            "odds_sources": ["proxy_source"],
            "context_sources": ["fixture_alias"],
        }
    }
    odds, contexts = patch._observed(row, {})
    assert odds == []
    assert contexts == []
