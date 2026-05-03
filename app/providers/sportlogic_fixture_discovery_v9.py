from __future__ import annotations

"""SportLogic fixture discovery v9: fast date fallback.

v8 confirmed cursor pagination works, but scanning several cursors for
`date=YYYY-MM-DD` walks backwards through older start_time rows and burns the
whole request budget before the next date is tried. SportLogic's `date` appears
to be a local-calendar date: `date=2026-05-03` returned late 2026-05-02 UTC
fixtures. Therefore the efficient strategy is:

* try the empirically working `date=YYYY-MM-DD` first;
* use cursor pagination, but only one cursor page by default;
* move to the next calendar date quickly so UTC-shifted fixtures can be found;
* keep the documented date_from/status variants as fallback, not as the hot path.
"""

import os
from datetime import datetime, timedelta
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v9_fast_date_fallback"


def _fast_cursor_scan_max() -> int:
    try:
        return max(1, min(4, int(float(os.getenv("SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX", "1") or 1))))
    except Exception:
        return 1


def _param_sets_fast(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        # Empirically working endpoint/parameter shape from smoke.
        {"date": date_key, "per_page": 100},
        # Useful when the API treats date_to as an exclusive upper bound.
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
        {"date_from": date_key, "per_page": 100},
        # Documented examples remain as fallback; they returned zero rows for
        # this key during smoke, so they are not the hot path.
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
