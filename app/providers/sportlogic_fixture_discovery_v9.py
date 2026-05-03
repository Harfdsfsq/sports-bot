from __future__ import annotations

"""SportLogic fixture discovery v13: documented games filters only.

Docs for GET /games list only these fixture filters: league_id, status,
date_from, date_to, is_live, per_page, cursor.  Previous probes used an
undocumented `date=YYYY-MM-DD` fallback. Smoke showed that this fallback returns
rows, but those rows stay at the same old start_time span. The likely reason is
simple: SportLogic ignores the unknown `date` parameter and returns the default
/games page ordered by start_time desc.

This patch removes `date`, `day`, `from/to`, `start_time_from`, `starts_after`
from production discovery. We now test only documented filters and cursor
pagination, so a smoke result with rows=0 means the API/key really is not
returning current fixtures via the documented contract.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v13_documented_games_filters"
WRAP_MARKER = "_harizon_sportlogic_documented_filter_wrappers_v13"


def _fast_cursor_scan_max() -> int:
    try:
        return max(1, min(6, int(float(os.getenv("SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX", "2") or 2))))
    except Exception:
        return 2


def _param_sets_documented(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        # Documented first-request shape for upcoming games.
        {"date_from": date_key, "status": "scheduled", "per_page": 100},
        # Bounded day window. date_to is documented and must be >= date_from.
        {"date_from": date_key, "date_to": next_key, "status": "scheduled", "per_page": 100},
        # Also allow live games for today's run window.
        {"date_from": date_key, "date_to": next_key, "status": "live", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "is_live": True, "per_page": 100},
        # No status fallback, still documented.
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
        {"date_from": date_key, "per_page": 100},
    ]


def _fixture_times(fixtures: list[dict[str, Any]]) -> list[datetime]:
    times: list[datetime] = []
    for row in fixtures:
        try:
            dt = v7._fixture_datetime(row)  # type: ignore[attr-defined]
            if dt is not None:
                times.append(dt.astimezone(UTC))
        except Exception:
            pass
    return sorted(times)


def _annotate_stats(provider: Any, stats: dict[str, Any], *, matches_built: int | None) -> None:
    fixtures = list(getattr(provider, "_fixture_cache", []) or [])
    if fixtures and int(stats.get("fixtures_fetched", 0) or 0) <= 0:
        stats["fixtures_fetched"] = len(fixtures)
        stats["games_fetched"] = len(fixtures)
    if not fixtures:
        stats.setdefault("empty_games_reason", "documented_games_filters_returned_no_rows")
        return
    times = _fixture_times(fixtures)
    if times:
        stats["inventory_min_start"] = times[0].isoformat()
        stats["inventory_max_start"] = times[-1].isoformat()
        stats["inventory_sample_start_times"] = [item.isoformat() for item in times[:5]]
    if matches_built is not None:
        out_of_window = int(stats.get("fixture_out_of_window", 0) or 0)
        if matches_built <= 0 and out_of_window >= len(fixtures):
            stats["inventory_status"] = "stale_inventory"
            stats["stale_inventory"] = True
            stats["empty_games_reason"] = "sportlogic_inventory_outside_runtime_horizon"
    else:
        if int(stats.get("events_matched", 0) or 0) <= 0 or int(stats.get("contexts_built", 0) or 0) <= 0:
            stats.setdefault("inventory_status", "unmatched_inventory")
            stats.setdefault("no_match_reason", "sportlogic_fixture_inventory_does_not_match_bootstrap")


def _install_stats_wrappers() -> bool:
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, WRAP_MARKER, False):
        return False

    original_fetch_matches = cls.fetch_matches
    original_fetch_offers = cls.fetch_offers
    original_fetch_context = cls.fetch_context

    async def fetch_matches_doc_filter_aware(self: Any):
        data, stats, preview = await original_fetch_matches(self)
        _annotate_stats(self, stats, matches_built=len(data or []))
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        if stats.get("empty_games_reason"):
            preview.setdefault("empty_games_reason", stats.get("empty_games_reason"))
        return data, stats, preview

    async def fetch_offers_doc_filter_aware(self: Any, matches: list[Any]):
        data, stats, preview = await original_fetch_offers(self, matches)
        _annotate_stats(self, stats, matches_built=None)
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        if stats.get("empty_games_reason"):
            preview.setdefault("empty_games_reason", stats.get("empty_games_reason"))
        return data, stats, preview

    async def fetch_context_doc_filter_aware(self: Any, matches: list[Any]):
        data, stats, preview = await original_fetch_context(self, matches)
        _annotate_stats(self, stats, matches_built=None)
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        if stats.get("empty_games_reason"):
            preview.setdefault("empty_games_reason", stats.get("empty_games_reason"))
        return data, stats, preview

    cls.fetch_matches = fetch_matches_doc_filter_aware
    cls.fetch_offers = fetch_offers_doc_filter_aware
    cls.fetch_context = fetch_context_doc_filter_aware
    setattr(cls, WRAP_MARKER, True)
    return True


def install() -> bool:
    v7._param_sets = _param_sets_documented  # type: ignore[attr-defined]
    v7._cursor_scan_max = _fast_cursor_scan_max  # type: ignore[attr-defined]
    v7.PATCH_MARKER = PATCH_MARKER
    installed = bool(v7.install())
    wrapped = _install_stats_wrappers()
    return installed or wrapped
