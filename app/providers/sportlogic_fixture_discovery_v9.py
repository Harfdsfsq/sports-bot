from __future__ import annotations

"""SportLogic fixture discovery v10-style parameter probe.

v9 proved that cursor pagination works and that `date=YYYY-MM-DD` returns rows,
but the returned rows stayed outside the target window for every date tested.
That means the provider's `date` parameter is not a reliable UTC fixture filter
for this key.  This module keeps the cheap one-cursor strategy and expands the
hot path to the plausible date filter aliases shown/ implied by the API shape:

* date=YYYY-MM-DD
* date_from/date_to, with same-day and next-day forms
* start_time_from/start_time_to
* starts_after/starts_before
* from/to and day

The file keeps the v9 import path so sitecustomize/smoke do not need another
wiring commit; the marker below tells diagnostics which behavior is active.
"""

import os
from datetime import datetime, timedelta
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v10_filter_alias_probe"


def _fast_cursor_scan_max() -> int:
    try:
        return max(1, min(3, int(float(os.getenv("SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX", "1") or 1))))
    except Exception:
        return 1


def _param_sets_fast(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        # Empirically returns rows, but can ignore the intended UTC date. Keep it
        # first because it confirms auth/parser quickly.
        {"date": date_key, "per_page": 100},
        # Date range variants. Some APIs use date_to inclusive, some exclusive.
        {"date_from": date_key, "date_to": date_key, "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
        {"date_from": date_key, "per_page": 100},
        # Explicit start-time aliases. The docs say `start_time` is UTC, while
        # filtering docs mention date_from/date_to; smoke suggests we need to
        # probe stricter start-time field names.
        {"start_time_from": f"{date_key}T00:00:00Z", "start_time_to": f"{next_key}T00:00:00Z", "per_page": 100},
        {"starts_after": f"{date_key}T00:00:00Z", "starts_before": f"{next_key}T00:00:00Z", "per_page": 100},
        {"from": date_key, "to": next_key, "per_page": 100},
        {"day": date_key, "per_page": 100},
        # Documented status forms. They returned zero rows in smoke, so they are
        # deliberately late in the list.
        {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "status": "scheduled", "per_page": 100},
        {"date": date_key, "status": "scheduled", "per_page": 100},
    ]


def install() -> bool:
    v7._param_sets = _param_sets_fast  # type: ignore[attr-defined]
    v7._cursor_scan_max = _fast_cursor_scan_max  # type: ignore[attr-defined]
    v7.PATCH_MARKER = PATCH_MARKER
    return bool(v7.install())
