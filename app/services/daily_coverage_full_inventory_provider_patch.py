from __future__ import annotations

"""Let coverage providers enrich the full day while prediction stays near-window.

`PredictionRunner` intentionally filters candidate modelling to the publication
window. Previously the same short list was also passed to odds/context APIs, making
300/300 daily coverage impossible. This patch stores the full discovered inventory
and only broadens provider calls selected by the strict daily plan. Candidate and
publication windows are untouched.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.daily_coverage_common import canonical_source, target_date
from app.services.daily_coverage_plan import filter_matches

_INSTALLED = False
_ORIGINAL_FILTER = None
_ORIGINAL_FETCH = None

_ODDS = {
    "odds_api_io",
    "sstats_pari",
    "bzzoiro",
    "allsportsapi",
    "sportlogic",
    "bookies_api",
    "rapidapi_odds",
    "sharpapi",
}
_CONTEXT = {
    "sstats",
    "bzzoiro",
    "clubelo",
    "sportlogic",
    "football_data",
    "espn",
    "openligadb",
    "thesportsdb",
    "openfootball",
    "api_football",
}


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        return canonical_source(runner._provider_name(provider))
    except Exception:
        module = getattr(getattr(provider, "__class__", None), "__module__", "")
        return canonical_source(module.rsplit(".", 1)[-1])


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


def _current_day_matches(runner: Any, matches: list[Any], now_utc: datetime) -> list[Any]:
    day = target_date(now_utc)
    tz = getattr(getattr(runner, "settings", None), "tzinfo", UTC)
    result: list[Any] = []
    for match in matches:
        kickoff = getattr(match, "commence_time", None)
        if not isinstance(kickoff, datetime):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        if kickoff.astimezone(tz).date().isoformat() != day:
            continue
        # Do not spend live request quota on long-finished fixtures. Their earlier
        # evidence remains available from the cumulative ledger/cache.
        if kickoff.astimezone(UTC) < now_utc - timedelta(minutes=20):
            continue
        result.append(match)
    return result


def install(prediction_runner: Any) -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FILTER, _ORIGINAL_FETCH
    if _INSTALLED:
        return {"status": "already_installed"}

    current_filter = prediction_runner._filter_matches
    current_fetch = prediction_runner._fetch_provider
    if getattr(current_fetch, "_harizon_full_inventory_provider_patch", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_FILTER = current_filter
    _ORIGINAL_FETCH = current_fetch

    def _filter_matches(self: Any, matches: list[Any], now_utc: datetime):
        assert callable(_ORIGINAL_FILTER)
        self._harizon_full_day_coverage_matches = _current_day_matches(self, list(matches or []), now_utc)
        return _ORIGINAL_FILTER(self, matches, now_utc)

    async def _fetch_provider(
        self: Any,
        provider: Any | None,
        method_name: str,
        *args: Any,
        empty_data: Any,
    ):
        assert callable(_ORIGINAL_FETCH)
        if provider is None or not args or not isinstance(args[0], list):
            return await _ORIGINAL_FETCH(self, provider, method_name, *args, empty_data=empty_data)
        name = _provider_name(self, provider)
        role_is_odds = "offer" in str(method_name).lower()
        eligible = name in (_ODDS if role_is_odds else _CONTEXT)
        if not eligible:
            return await _ORIGINAL_FETCH(self, provider, method_name, *args, empty_data=empty_data)

        broad = _dedupe(getattr(self, "_harizon_full_day_coverage_matches", []), args[0])
        planned = list(filter_matches(name, method_name, broad) or [])
        call_args = list(args)
        call_args[0] = planned or list(args[0])
        data, stats, preview = await _ORIGINAL_FETCH(
            self,
            provider,
            method_name,
            *call_args,
            empty_data=empty_data,
        )
        if isinstance(stats, dict):
            stats["full_day_coverage_pool"] = len(broad)
            stats["full_day_coverage_targets"] = len(call_args[0])
            stats["candidate_publish_window_targets"] = len(args[0])
            stats["publication_window_relaxed"] = False
        return data, stats, preview

    _filter_matches._harizon_full_inventory_filter_capture = True
    _fetch_provider._harizon_full_inventory_provider_patch = True
    prediction_runner._filter_matches = _filter_matches
    prediction_runner._fetch_provider = _fetch_provider
    _INSTALLED = True
    return {
        "status": "installed",
        "odds_providers": sorted(_ODDS),
        "context_providers": sorted(_CONTEXT),
        "candidate_publication_window_unchanged": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
