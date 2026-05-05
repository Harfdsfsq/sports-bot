from __future__ import annotations

"""SportLogic request fallback guard.

SportLogic can return HTTP 200 with an empty data list for one date-parameter
shape while the key is valid.  This runtime patch expands fixture discovery with
several conservative parameter variants before the provider gives up.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_query_guard_v1"


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _dedupe_rows(rows: list[dict[str, Any]], provider: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            key = str(provider._event_id(row) or "").strip()
        except Exception:
            key = ""
        if not key:
            key = repr(sorted(row.items()))[:240]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _param_variants_for_date(date_key: str, per_page: int = 100) -> list[dict[str, Any]]:
    return [
        {"date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"from": date_key, "to": date_key, "per_page": per_page},
        {"start_date": date_key, "end_date": date_key, "per_page": per_page},
        {"date": date_key, "per_page": per_page},
        {"starts_at_from": f"{date_key}T00:00:00Z", "starts_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
        {"status": "scheduled", "date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"status": "pending", "date_from": date_key, "date_to": date_key, "per_page": per_page},
    ]


def _broad_param_variants(per_page: int = 100) -> list[dict[str, Any]]:
    return [
        {"per_page": per_page},
        {"status": "scheduled", "per_page": per_page},
        {"status": "pending", "per_page": per_page},
        {"sport": "football", "per_page": per_page},
        {"sport": "soccer", "per_page": per_page},
    ]


def install() -> bool:
    if not _truthy(os.getenv("SPORTLOGIC_QUERY_GUARD_ENABLED"), True):
        return False
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    original_load = getattr(cls, "_load_fixtures_for_matches", None)
    if not callable(original_load):
        return False

    async def load_fixtures_for_matches_patched(self: Any, matches: list[Any]):
        stats = self._stats("fixtures")
        preview: dict[str, Any] = {"sample_fixtures": [], "errors": [], "query_variants_used": []}
        dates = sorted({m.commence_time.astimezone(UTC).date().isoformat() for m in matches or []})[:6]
        fixtures: list[dict[str, Any]] = []
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for date_key in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_rows: list[dict[str, Any]] = []
                for params in _param_variants_for_date(date_key):
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await self._get_json(client, "/games", params, stats, preview)
                    rows = self._extract_list(payload)
                    if rows:
                        day_rows.extend(rows)
                        preview["query_variants_used"].append({"date": date_key, "params": params, "rows": len(rows)})
                        break
                fixtures.extend(day_rows)
            if not fixtures and _truthy(os.getenv("SPORTLOGIC_BROAD_FALLBACK_ENABLED"), True):
                for params in _broad_param_variants():
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await self._get_json(client, "/games", params, stats, preview)
                    rows = self._extract_list(payload)
                    if rows:
                        fixtures.extend(rows)
                        preview["query_variants_used"].append({"date": "broad", "params": params, "rows": len(rows)})
                        break

        fixtures = _dedupe_rows(fixtures, self)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["sportlogic_query_guard_enabled"] = True
        stats["query_variants_used"] = preview["query_variants_used"][:8]
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    cls._load_fixtures_for_matches = load_fixtures_for_matches_patched
    setattr(cls, PATCH_MARKER, True)
    return True
