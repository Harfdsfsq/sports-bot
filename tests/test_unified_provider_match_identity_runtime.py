from __future__ import annotations

from datetime import datetime, timezone


def test_unified_identity_merges_provider_aliases_and_preserves_query_map(monkeypatch):
    monkeypatch.setenv("DAY_INVENTORY_ALLOW_LOW_TIER", "1")
    from scripts import build_day_inventory_core as core
    from app.services.unified_provider_match_identity_runtime import merge_matches_unified

    kickoff = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    odds = core.make_match(
        source="odds_api_io",
        source_event_id="odds-777",
        league_name="International Friendly",
        home_team="Brazil FC",
        away_team="England United",
        commence_time=kickoff,
        metadata={"has_current_odds_provider": True},
    )
    bzz = core.make_match(
        source="bzzoiro",
        source_event_id="bz-888",
        league_name="International Clubs - Friendly",
        home_team="Brasil",
        away_team="England",
        commence_time=kickoff,
        metadata={"bzzoiro_has_context_hint": True},
    )

    assert odds is not None and bzz is not None
    merged, crosswalk = merge_matches_unified(
        {"odds_api_io": [odds], "bzzoiro": [bzz], "sstats": [], "sportlogic": []},
        object(),
    )

    assert len(merged) == 1
    meta = merged[0].metadata
    assert meta["provider_source_ids"] == {"odds_api_io": "odds-777", "bzzoiro": "bz-888"}
    assert set(str(meta["sources_seen"]).split(",")) == {"odds_api_io", "bzzoiro"}
    assert meta["provider_query_map"]["odds_api_io"]["event_id"] == "odds-777"
    assert meta["provider_query_map"]["bzzoiro"]["event_id"] == "bz-888"
    assert crosswalk["algorithm"] == "match_identity_v2"
    assert len(crosswalk["matched_rows"]) == 1


def test_runtime_installer_patches_core_merge(monkeypatch):
    monkeypatch.setenv("DAY_INVENTORY_ALLOW_LOW_TIER", "1")
    from scripts import build_day_inventory_core as core
    from app.services import unified_provider_match_identity_runtime as patch

    result = patch.install()

    assert result["installed"] is True
    assert core.merge_matches is patch.merge_matches_unified
