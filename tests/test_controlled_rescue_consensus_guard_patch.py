from __future__ import annotations

from types import SimpleNamespace


def test_controlled_rescue_blocks_negative_consensus_value(monkeypatch):
    import app.services.controlled_candidate_rescue as rescue
    from app.services import controlled_rescue_consensus_guard_patch as patch

    patch._INSTALLED = False
    patch.install()

    rejections: dict[str, int] = {}
    match = SimpleNamespace(
        match_key="m1",
        sport_key="soccer",
        league_name="Test League",
        home_team="Home",
        away_team="Away",
        commence_time="2026-05-22T18:00:00+00:00",
        source_event_id="m1",
    )
    bucket = [
        SimpleNamespace(source="odds_api_io", bookmaker="Bet365", family="totals", selection="Under", price=2.50, point=2.5, team_side=None, market_name="total", source_event_id="m1"),
    ]
    opposite = [
        SimpleNamespace(source="odds_api_io", bookmaker="Bet365", family="totals", selection="Over", price=1.55, point=2.5, team_side=None, market_name="total", source_event_id="m1"),
    ]

    row = rescue._make_candidate(
        match=match,
        family="totals",
        selection="Under",
        point=2.5,
        bucket=bucket,
        opposite=opposite,
        consensus_prob=0.38,  # below selected implied 40.0%
        paired_books=1,
        context=None,
        rejections=rejections,
    )

    assert row is None
    assert rejections.get("controlled_rescue_consensus_value_guard") == 1
