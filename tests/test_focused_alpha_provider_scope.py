from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import daily_coverage_full_inventory_provider_patch as scope
from app.services import daily_coverage_plan as coverage_plan


def test_explicit_empty_assignment_is_declared(monkeypatch) -> None:
    monkeypatch.setattr(
        scope,
        "load_plan",
        lambda: {
            "assignments": {
                "bzzoiro": {"offers": [], "context": []},
            }
        },
    )

    assert scope._assignment_declared("bzzoiro", "get_context") is True
    assert scope._assignment_declared("bzzoiro", "get_offers") is True
    assert scope._assignment_declared("sstats", "get_context") is False


def test_model_scope_uses_only_target_match_keys(monkeypatch) -> None:
    monkeypatch.setattr(
        scope,
        "load_plan",
        lambda: {
            "focused_alpha": {"mode": "focused_alpha_information_value"},
            "fixed_300_provider_target": False,
            "target_match_keys": ["2026-07-24|team a|team b"],
        },
    )
    runner = SimpleNamespace(settings=SimpleNamespace(tzinfo=UTC))
    matches = [
        SimpleNamespace(
            match_key="soccer|team a|team b|2026-07-24",
            home_team="Team A",
            away_team="Team B",
            commence_time=datetime(2026, 7, 24, 12, tzinfo=UTC),
        ),
        SimpleNamespace(
            match_key="soccer|team c|team d|2026-07-24",
            home_team="Team C",
            away_team="Team D",
            commence_time=datetime(2026, 7, 24, 13, tzinfo=UTC),
        ),
    ]

    selected, declared = scope._focused_model_scope(runner, matches)

    assert declared is True
    assert [match.home_team for match in selected] == ["Team A"]


def test_explicit_empty_focus_cohort_models_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        scope,
        "load_plan",
        lambda: {
            "focused_alpha": {"mode": "focused_alpha_information_value"},
            "fixed_300_provider_target": False,
            "target_match_keys": [],
        },
    )
    runner = SimpleNamespace(settings=SimpleNamespace(tzinfo=UTC))

    selected, declared = scope._focused_model_scope(runner, [SimpleNamespace(match_key="x")])

    assert declared is True
    assert selected == []


def test_legacy_diacritic_key_uses_exact_selected_identity(monkeypatch) -> None:
    broken_key = "2026-07-31|deportivo alav s|castell n"
    plan = {
        "focused_alpha": {
            "mode": "focused_alpha_information_value",
            "selected": [
                {
                    "match_key": broken_key,
                    "home_team": "Deportivo Alavés",
                    "away_team": "CD Castellón",
                    "kickoff_utc": "2026-07-31T12:00:00+00:00",
                }
            ],
        },
        "fixed_300_provider_target": False,
        "target_match_keys": [broken_key],
        "assignments": {"sstats": {"context": [broken_key]}},
    }
    monkeypatch.setattr(scope, "load_plan", lambda: plan)
    monkeypatch.setattr(coverage_plan, "load_plan", lambda: plan)
    runner = SimpleNamespace(settings=SimpleNamespace(tzinfo=UTC))
    target = SimpleNamespace(
        match_key="soccer|alaves|castellon|2026-07-31",
        home_team="Alaves",
        away_team="Castellón",
        commence_time=datetime(2026, 7, 31, 12, tzinfo=UTC),
        metadata={},
    )
    unrelated = SimpleNamespace(
        match_key="soccer|albacete|castellon|2026-07-31",
        home_team="Albacete",
        away_team="Castellón",
        commence_time=datetime(2026, 7, 31, 12, tzinfo=UTC),
        metadata={},
    )

    selected, declared = scope._focused_model_scope(runner, [target, unrelated])

    assert declared is True
    assert selected == [target]
    assert coverage_plan.filter_matches(
        "sstats", "fetch_context", [target, unrelated]
    ) == [target]
