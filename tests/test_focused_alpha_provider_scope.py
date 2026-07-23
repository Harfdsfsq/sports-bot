from __future__ import annotations

from app.services import daily_coverage_full_inventory_provider_patch as scope


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
