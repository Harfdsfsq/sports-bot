from __future__ import annotations

from app.services import focused_alpha_assignments as routing


def _row() -> dict:
    return {
        "match_key": "2026-07-24|team a|team b",
        "home_team": "Team A",
        "away_team": "Team B",
        "league_name": "UEFA Champions League",
        "hours_to_kickoff": 10.0,
        "provider_assignment_eligible": True,
        "odds_sources": [],
        "context_sources": [],
        "focused_alpha_score": 80.0,
        "metadata": {
            "day_inventory_source_ids": {
                "bzzoiro": "bzz-1",
                "espn": "espn-1",
            }
        },
    }


def test_routing_chooses_small_provider_subset(monkeypatch) -> None:
    monkeypatch.setattr(routing, "atomic_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        routing,
        "_provider_health",
        lambda: {
            "sportlogic": {
                "usable": False,
                "diagnosis": "active_odds_stale_only_no_current_fixture",
            },
            "bzzoiro": {"usable": True},
        },
    )

    assignments = routing.build_focused_assignments([_row()], run_index=1)

    odds_selected = [
        provider
        for provider in routing._ODDS_PROVIDERS
        if assignments[provider]["offers"]
    ]
    context_selected = [
        provider
        for provider in routing._CONTEXT_PROVIDERS
        if assignments[provider]["context"]
    ]
    assert len(odds_selected) == 2
    assert len(context_selected) == 2
    assert "bzzoiro" in odds_selected
    assert "espn" in context_selected
    assert assignments["sportlogic"]["offers"] == []
    assert assignments["sportlogic"]["context"] == []


def test_second_run_can_try_three_context_providers(monkeypatch) -> None:
    monkeypatch.setattr(routing, "atomic_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        routing,
        "_provider_health",
        lambda: {"sportlogic": {"usable": False}, "bzzoiro": {"usable": True}},
    )

    assignments = routing.build_focused_assignments([_row()], run_index=2)

    context_selected = sum(
        bool(assignments[provider]["context"])
        for provider in routing._CONTEXT_PROVIDERS
    )
    assert context_selected == 3
