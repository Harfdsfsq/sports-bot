from __future__ import annotations

from typing import Any

from app.services.daily_coverage_common import canonical_source
from app.services.daily_coverage_plan import filter_matches

_INSTALLED = False
_ORIGINAL_SELECT = None

_CORE_CONTEXT = {
    "sstats",
    "bzzoiro",
    "clubelo",
    "sportlogic",
    "football_data",
    "espn",
    "openligadb",
    "thesportsdb",
    "openfootball",
}


def _dedupe(*groups: list[Any] | None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        for match in list(group or []):
            key = str(getattr(match, "match_key", ""))
            if key and key not in seen:
                seen.add(key)
                out.append(match)
    return out


def _select(
    self: Any,
    matches: list[Any],
    provider_name: str,
    *,
    fallback_matches: list[Any] | None = None,
    offers_by_match: dict[str, list[Any]] | None = None,
) -> list[Any]:
    assert callable(_ORIGINAL_SELECT)
    dynamic = getattr(self, "_harizon_dynamic_provider_slots", {}) or {}
    actual = canonical_source(dynamic.get(provider_name) or provider_name)
    if actual in _CORE_CONTEXT:
        broad = _dedupe(fallback_matches, matches)
        planned = list(filter_matches(actual, "fetch_context", broad) or [])
        if planned:
            return planned
    return list(
        _ORIGINAL_SELECT(
            self,
            matches,
            provider_name,
            fallback_matches=fallback_matches,
            offers_by_match=offers_by_match,
        )
        or []
    )


def install(prediction_runner: Any) -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SELECT
    if _INSTALLED:
        return {"status": "already_installed"}
    current = prediction_runner._select_provider_context_matches
    if getattr(current, "_harizon_daily_core_target_patch", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_SELECT = current
    _select._harizon_daily_core_target_patch = True
    prediction_runner._select_provider_context_matches = _select
    _INSTALLED = True
    return {
        "status": "installed",
        "core_context_providers": sorted(_CORE_CONTEXT),
        "selection_policy": "daily_plan_before_offer_shortlist",
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
