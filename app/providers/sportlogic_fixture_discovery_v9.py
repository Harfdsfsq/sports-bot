from __future__ import annotations

"""SportLogic fixture discovery v12: stale-inventory aware diagnostics.

Current smoke facts:
* auth works;
* /games works;
* cursor pagination works;
* native row parser works;
* returned inventory is outside the active runtime horizon and does not match
  the bootstrap matches.

This module keeps the safe one-cursor strategy, preserves stale raw inventory for
smoke diagnostics, and labels it explicitly as `stale_inventory`.  The normal
runner still must not publish old fixtures as current matches.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v12_stale_inventory_status"
WRAP_MARKER = "_harizon_sportlogic_stale_inventory_wrappers_v12"


def _fast_cursor_scan_max() -> int:
    try:
        return max(1, min(3, int(float(os.getenv("SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX", "1") or 1))))
    except Exception:
        return 1


def _inventory_lookback_hours() -> int:
    try:
        return max(0, min(96, int(float(os.getenv("SPORTLOGIC_INVENTORY_LOOKBACK_HOURS", "48") or 48))))
    except Exception:
        return 48


def _inventory_lookahead_days() -> int:
    try:
        return max(1, min(7, int(float(os.getenv("SPORTLOGIC_INVENTORY_LOOKAHEAD_DAYS", "4") or 4))))
    except Exception:
        return 4


def _target_window_inventory(matches: list[Any], now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    starts: list[datetime] = []
    for match in matches:
        try:
            starts.append(match.commence_time.astimezone(UTC))
        except Exception:
            pass
    if starts:
        return min(starts) - timedelta(hours=_inventory_lookback_hours()), max(starts) + timedelta(days=_inventory_lookahead_days())
    return now - timedelta(hours=_inventory_lookback_hours()), now + timedelta(days=_inventory_lookahead_days())


def _param_sets_fast(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        {"date": date_key, "per_page": 100},
        {"date_from": date_key, "date_to": date_key, "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
        {"date_from": date_key, "per_page": 100},
        {"start_time_from": f"{date_key}T00:00:00Z", "start_time_to": f"{next_key}T00:00:00Z", "per_page": 100},
        {"starts_after": f"{date_key}T00:00:00Z", "starts_before": f"{next_key}T00:00:00Z", "per_page": 100},
        {"from": date_key, "to": next_key, "per_page": 100},
        {"day": date_key, "per_page": 100},
        {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "status": "scheduled", "per_page": 100},
        {"date": date_key, "status": "scheduled", "per_page": 100},
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
            stats.setdefault("inventory_status", "stale_inventory_or_unmatched_inventory")
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

    async def fetch_matches_stale_aware(self: Any):
        data, stats, preview = await original_fetch_matches(self)
        _annotate_stats(self, stats, matches_built=len(data or []))
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        return data, stats, preview

    async def fetch_offers_stale_aware(self: Any, matches: list[Any]):
        data, stats, preview = await original_fetch_offers(self, matches)
        _annotate_stats(self, stats, matches_built=None)
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        return data, stats, preview

    async def fetch_context_stale_aware(self: Any, matches: list[Any]):
        data, stats, preview = await original_fetch_context(self, matches)
        _annotate_stats(self, stats, matches_built=None)
        if stats.get("inventory_status"):
            preview.setdefault("inventory_status", stats.get("inventory_status"))
        return data, stats, preview

    cls.fetch_matches = fetch_matches_stale_aware
    cls.fetch_offers = fetch_offers_stale_aware
    cls.fetch_context = fetch_context_stale_aware
    setattr(cls, WRAP_MARKER, True)
    return True


def install() -> bool:
    v7._param_sets = _param_sets_fast  # type: ignore[attr-defined]
    v7._cursor_scan_max = _fast_cursor_scan_max  # type: ignore[attr-defined]
    v7._target_window = _target_window_inventory  # type: ignore[attr-defined]
    v7.PATCH_MARKER = PATCH_MARKER
    installed = bool(v7.install())
    wrapped = _install_stats_wrappers()
    return installed or wrapped
