from __future__ import annotations

from types import SimpleNamespace

from app.services.targeted_enrichment_runtime_patch import call_without_offer_gate, merge_target_pools


def test_merge_target_pools_keeps_fallback_rows_after_base_rows() -> None:
    base = [SimpleNamespace(match_key="offered")]
    current = [SimpleNamespace(match_key="offered"), SimpleNamespace(match_key="current-no-offer")]
    fallback = [SimpleNamespace(match_key="inventory-no-offer")]

    merged = merge_target_pools(base, current, fallback)

    assert [item.match_key for item in merged] == [
        "offered",
        "current-no-offer",
        "inventory-no-offer",
    ]


def test_context_offer_gate_is_temporarily_disabled_and_restored() -> None:
    settings = SimpleNamespace(context_enrichment_requires_offers=True)
    seen: list[bool] = []

    def callback() -> str:
        seen.append(settings.context_enrichment_requires_offers)
        return "selected"

    assert call_without_offer_gate(settings, callback) == "selected"
    assert seen == [False]
    assert settings.context_enrichment_requires_offers is True
