from __future__ import annotations

"""SportLogic fixture discovery v11: stale-inventory aware probe.

Smoke proved that SportLogic auth, /games, cursor pagination and row parsing all
work, but every date/filter alias currently returns the same stale page around
2026-05-02 20:00-23:30 UTC.  Returning `EMPTY_FIXTURES` for this is misleading:
the provider is alive but does not expose current/future fixtures for the target
window.

This module keeps the cheap one-cursor strategy and widens the fixture discovery
window enough to retain raw stale inventory.  The normal runner still filters out
old matches before publishing, but smoke can now distinguish:

* provider/API dead -> EMPTY/AUTH/HTTP_ERROR
* provider alive but stale -> STALE_INVENTORY / NO_MATCH
* provider alive and current -> OK
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v11_stale_inventory_aware"


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


def install() -> bool:
    v7._param_sets = _param_sets_fast  # type: ignore[attr-defined]
    v7._cursor_scan_max = _fast_cursor_scan_max  # type: ignore[attr-defined]
    v7._target_window = _target_window_inventory  # type: ignore[attr-defined]
    v7.PATCH_MARKER = PATCH_MARKER
    return bool(v7.install())
