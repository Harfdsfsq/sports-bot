from __future__ import annotations

import os
from typing import Any

from app.services.daily_coverage_common import canonical_source
from app.services.daily_coverage_plan import filter_matches

_ORIGINAL_INIT = None
_ORIGINAL_SELECT = None
_ORIGINAL_PROVIDER_TARGETS = None


def _init(self: Any, settings: Any) -> None:
    assert callable(_ORIGINAL_INIT)
    _ORIGINAL_INIT(self, settings)
    dynamic: dict[str, str] = {}
    try:
        from app.providers.sstats_pari_odds import SStatsPariOddsProvider

        for slot in ("oddspapi", "allsportsapi", "bookies_api"):
            if getattr(self, slot, None) is None:
                setattr(self, slot, SStatsPariOddsProvider(settings))
                dynamic[slot] = "sstats_pari"
                break
    except Exception as exc:
        dynamic["sstats_pari_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from app.providers.clubelo import ClubEloContextProvider

        for slot in ("futrixmetrics", "gnews", "newsapi", "openfootball"):
            if getattr(self, slot, None) is None:
                setattr(self, slot, ClubEloContextProvider(settings))
                dynamic[slot] = "clubelo"
                os.environ["HARIZON_CLUBELO_RUNNER_SLOT"] = slot
                break
    except Exception as exc:
        dynamic["clubelo_error"] = f"{type(exc).__name__}: {exc}"
    self._harizon_dynamic_provider_slots = dynamic


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
    if provider_name in dynamic:
        pool = list(fallback_matches or []) + list(matches or [])
        deduped, seen = [], set()
        for match in pool:
            key = str(getattr(match, "match_key", ""))
            if key and key not in seen:
                seen.add(key)
                deduped.append(match)
        return list(filter_matches(actual, "fetch_context", deduped) or [])
    selected = _ORIGINAL_SELECT(
        self,
        matches,
        provider_name,
        fallback_matches=fallback_matches,
        offers_by_match=offers_by_match,
    )
    return list(filter_matches(actual, "fetch_context", list(selected or [])) or [])


def _provider_targets(
    self: Any,
    provider_key: str,
    targets: list[Any],
    offers_by_match: dict[str, list[Any]] | None = None,
) -> list[Any]:
    assert callable(_ORIGINAL_PROVIDER_TARGETS)
    slot = str(os.getenv("HARIZON_CLUBELO_RUNNER_SLOT") or "").strip().lower()
    if slot and str(provider_key or "").strip().lower() == slot:
        return list(targets or [])
    return _ORIGINAL_PROVIDER_TARGETS(self, provider_key, targets, offers_by_match)


def install(prediction_runner: Any, coverage_planner: Any) -> dict[str, Any]:
    global _ORIGINAL_INIT, _ORIGINAL_SELECT, _ORIGINAL_PROVIDER_TARGETS
    _ORIGINAL_INIT = prediction_runner.__init__
    _ORIGINAL_SELECT = prediction_runner._select_provider_context_matches
    _ORIGINAL_PROVIDER_TARGETS = coverage_planner.provider_targets
    prediction_runner.__init__ = _init
    prediction_runner._select_provider_context_matches = _select
    coverage_planner.provider_targets = _provider_targets
    return {
        "dynamic_odds_provider": "sstats_pari",
        "dynamic_context_provider": "clubelo",
    }
