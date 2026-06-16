from __future__ import annotations

from scripts import apply_sstats_deep_inventory_enrichment_v4 as deep


def test_extract_expected_goals_from_last_games_stats() -> None:
    home, away, source = deep.extract_expected_goals(
        (
            "last_games_stats",
            {
                "home": {"xg": 1.42},
                "away": {"xg": 1.08},
            },
        )
    )

    assert (home, away, source) == (1.42, 1.08, "last_games_stats")


def test_mark_persists_sstats_expected_goals() -> None:
    row = {"coverage": {}}

    deep.mark(
        row,
        "1520725",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        last_stats_payload={
            "home": {"expectedGoals": 1.55},
            "away": {"expectedGoals": 0.92},
        },
    )

    assert row["expected_home"] == 1.55
    assert row["expected_away"] == 0.92
    assert row["sstats_xg_source"] == "last_games_stats"
    assert row["coverage"]["xg"] is True
    assert "sstats" in row["xg_sources"]


def test_mark_does_not_claim_xg_without_numeric_pair() -> None:
    row = {"coverage": {}}

    deep.mark(
        row,
        "1520725",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        last_stats_payload={"home": {"shots": 12}, "away": {"shots": 8}},
    )

    assert "expected_home" not in row
    assert "expected_away" not in row
    assert row["coverage"]["context"] is True
    assert row["coverage"]["xg"] is False
    assert "xg_sources" not in row
