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


def test_run_window_bridge_spends_budget_on_nearest_bucket_first(monkeypatch) -> None:
    monkeypatch.setattr(routing, "_provider_health", lambda: {"sportlogic": {"usable": False}})
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", "1")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", "10")
    rows = [
        _row("outside-high-score", 100),
        _row("window-low-score", 5),
    ]
    rows[0]["hours_to_kickoff"] = 3
    rows[1]["hours_to_kickoff"] = 1
    rows[1]["focused_alpha_run_window_bridge"] = True

    assignments = routing.build_focused_assignments(rows, 1)

    assert assignments["odds_api_io"]["offers"] == ["window-low-score"]


def test_coverage_backlog_fills_empty_rows_before_refreshing_covered_rows(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        routing, "_provider_health", lambda: {"sportlogic": {"usable": False}}
    )
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", "1")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SPORTLOGIC_OFFERS_BUDGET", "0")
    rows = [
        _row(
            "covered-high-score",
            100,
            odds=["odds_api_io", "bzzoiro"],
            contexts=["sstats", "bzzoiro"],
            odds_age_minutes=900,
            context_age_minutes=900,
        ),
        _row("empty-low-score", 1),
    ]
    for row in rows:
        row.update(
            {
                "hours_to_kickoff": 6,
                "provider_coverage_backlog": True,
                "line_deficit": max(0, 2 - len(row["odds_sources"])),
                "context_deficit": max(0, 2 - len(row["context_sources"])),
            }
        )

    assignments = routing.build_focused_assignments(rows, 7)

    assert assignments["odds_api_io"]["offers"] == ["empty-low-score"]
    assert "covered-high-score" not in _assigned_keys(assignments, "offers")


def test_stale_sportlogic_probe_allows_bounded_bootstrap(monkeypatch) -> None:
    monkeypatch.setenv("SPORTLOGIC_API_KEY", "configured")
    monkeypatch.setenv("SPORTLOGIC_ENABLED", "true")
    monkeypatch.delenv("SPORTLOGIC_DAILY_CIRCUIT_OPEN", raising=False)
    monkeypatch.setattr(
        routing,
        "_load",
        lambda _path: {
            "created_at_utc": "2026-07-20T00:00:00+00:00",
            "diagnosis": "active_odds_stale_only_no_current_fixture",
            "matched_games": 0,
        },
    )

    health = routing._provider_health()["sportlogic"]

    assert health["usable"] is True
    assert health["bootstrap_probe"] is True


def test_coverage_backlog_can_route_120_odds_refreshes(monkeypatch) -> None:
    monkeypatch.setattr(
        routing, "_provider_health", lambda: {"sportlogic": {"usable": False}}
    )
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", "120")
    monkeypatch.setenv("FOCUSED_ALPHA_DOUBLE_ODDS_REFRESH_MATCHES", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", "120")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SPORTLOGIC_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_CONTEXT_ENRICH_MATCHES", "1")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_CONTEXT_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_CONTEXT_BUDGET", "0")
    rows = []
    for index in range(130):
        row = _row(f"empty-{index:03d}", 1)
        row.update(
            {
                "hours_to_kickoff": 6,
                "provider_coverage_backlog": True,
                "line_deficit": 2,
                "context_deficit": 2,
            }
        )
        rows.append(row)

    assignments = routing.build_focused_assignments(rows, 7)

    assert len(assignments["odds_api_io"]["offers"]) == 120


def test_sportlogic_bootstrap_gets_bounded_repair_lane(monkeypatch) -> None:
    monkeypatch.setattr(
        routing,
        "_provider_health",
        lambda: {"sportlogic": {"usable": True, "bootstrap_probe": True}},
    )
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", "10")
    monkeypatch.setenv("FOCUSED_ALPHA_DOUBLE_ODDS_REFRESH_MATCHES", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", "10")
    monkeypatch.setenv("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_SPORTLOGIC_OFFERS_BUDGET", "3")
    rows = []
    for index in range(5):
        row = _row(f"gap-{index}", 1)
        row.update(
            {
                "hours_to_kickoff": index + 1,
                "provider_coverage_backlog": True,
                "line_deficit": 2,
                "context_deficit": 2,
            }
        )
        rows.append(row)

    assignments = routing.build_focused_assignments(rows, 7)

    assert assignments["sportlogic"]["offers"] == ["gap-0", "gap-1", "gap-2"]
