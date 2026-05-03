from __future__ import annotations

"""SportLogic fixture discovery v8.

v7 fixed the main documented issue: SportLogic uses cursor pagination, not
`page`. Smoke after v7 showed another provider-specific issue: requests with
`status=scheduled` returned zero rows, while the earlier working probe
`date=YYYY-MM-DD` returned rows.  Therefore v8 keeps cursor pagination but adds
working fallback parameter sets without `status`.
"""

from datetime import datetime, timedelta
from typing import Any

from app.providers import sportlogic_fixture_discovery_v7 as v7

PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v8_cursor_date_fallback"


def _param_sets_with_fallback(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        # Documented examples first.
        {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "status": "scheduled", "per_page": 100},
        # Reality fallback from smoke: this returns SportLogic rows while the
        # scheduled/status filters can return an empty dataset.
        {"date": date_key, "per_page": 100},
        {"date": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
        {"date_from": date_key, "per_page": 100},
    ]


def install() -> bool:
    # v7.install() closes over v7._param_sets, so replacing it before install
    # changes discovery behavior without duplicating the large parser code.
    v7._param_sets = _param_sets_with_fallback  # type: ignore[attr-defined]
    original_marker = getattr(v7, "PATCH_MARKER", "")
    v7.PATCH_MARKER = PATCH_MARKER
    try:
        return bool(v7.install())
    finally:
        # Keep the marker visible on v7 after install for diagnostics.
        v7.PATCH_MARKER = PATCH_MARKER or original_marker
