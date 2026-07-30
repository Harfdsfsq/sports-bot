from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import focused_alpha_assignments_v2 as routing


def _row(
    key: str,
    score: float,
    *,
    odds: list[str] | None = None,
    contexts: list[str] | None = None,
    odds_age_minutes: int | None = None,
    context_age_minutes: int | None = None,
) -> dict:
    now = datetime.now(UTC)
    refresh: dict[str, str] = {}
    if odds_age_minutes is not None:
        refresh["last_odds_refresh_utc"] = (
            now - timedelta(minutes=odds_age_minutes)
        ).isoformat()
    if context_age_minutes is not None:
        refresh["last_context_refresh_utc"] = (
            now - timedelta(minutes=context_age_minutes)
        ).isoformat()
    return {
        "match_key": key,
        "home_team": f"Home {key}",
        "away_team": f"Away {key}",
        "league_name": "Premier League",
        "focused_alpha_score": score,
        "hours_to_kickoff": 8,
        "odds_sources": odds or [],
        "context_sources": contexts or [],
        "refresh": refresh,
    }


def _assigned_keys(assignments: dict, role: str) -> set[str]:
    return {
        key
        for roles in assignments.values()
        for key in roles.get(role, [])
    }


def test_stale_three_source_match_still_gets_current_market_refresh(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    row = _row(
        "stale-top",
        100,
        odds=["odds_api_io", "bzzoiro", "sstats_pari"],
        contexts=["sstats"],
        odds_age_minutes=600,
        context_age_minutes=60,
    )

    assignments = routing.build_focused_assignments([row], 5)

    assert "stale-top" in _assigned_keys(assignments, "offers")


def test_fresh_match_outside_odds_lane_is_not_refreshed(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", "1")
    monkeypatch.setenv("FOCUSED_ALPHA_DOUBLE_ODDS_REFRESH_MATCHES", "1")
    rows = [
        _row("top", 100, odds=["odds_api_io"], contexts=["sstats"], odds_age_minutes=5),
        _row(
            "fresh-outside",
            90,
            odds=["odds_api_io", "bzzoiro", "sstats_pari"],
            contexts=["sstats"],
            odds_age_minutes=5,
            context_age_minutes=5,
        ),
    ]

    assignments = routing.build_focused_assignments(rows, 5)

    assert "fresh-outside" not in _assigned_keys(assignments, "offers")


def test_context_enrichment_is_bounded_to_priority_lane(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    monkeypatch.setenv("FOCUSED_ALPHA_CONTEXT_ENRICH_MATCHES", "2")
    monkeypatch.setenv("FOCUSED_ALPHA_DOUBLE_CONTEXT_MATCHES", "2")
    rows = [
        _row(
            f"match-{index}",
            100 - index,
            odds=["odds_api_io"],
            contexts=[],
            odds_age_minutes=120,
        )
        for index in range(5)
    ]

    assignments = routing.build_focused_assignments(rows, 5)

    assert _assigned_keys(assignments, "context") <= {"match-0", "match-1"}


def test_fresh_single_context_is_supplemented_by_another_provider(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    row = _row(
        "single-context",
        100,
        odds=["odds_api_io"],
        contexts=["sstats"],
        odds_age_minutes=5,
        context_age_minutes=5,
    )

    assignments = routing.build_focused_assignments([row], 5)

    assert "single-context" not in assignments["sstats"]["context"]
    assert "single-context" in _assigned_keys(assignments, "context")


def test_stale_existing_context_provider_can_be_refreshed(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    row = _row(
        "stale-context",
        100,
        odds=["odds_api_io"],
        contexts=["sstats", "bzzoiro"],
        odds_age_minutes=5,
        context_age_minutes=900,
    )

    assignments = routing.build_focused_assignments([row], 5)

    assert "stale-context" in assignments["sstats"]["context"]


def test_bootstrap_routing_spends_budget_on_nearest_bucket_first(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", "1")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", "10")
    rows = [
        _row("far-high-score", 100),
        _row("near-low-score", 5),
    ]
    rows[0]["hours_to_kickoff"] = 18
    rows[1]["hours_to_kickoff"] = 3
    for row in rows:
        row["focused_alpha_bootstrap"] = True

    assignments = routing.build_focused_assignments(rows, 1)

    assert assignments["odds_api_io"]["offers"] == ["near-low-score"]
