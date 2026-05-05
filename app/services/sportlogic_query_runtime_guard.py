from __future__ import annotations

"""SportLogic request fallback guard.

SportLogic may return HTTP 200 with an empty data list for dated game queries,
while a broad /games request returns usable fixtures. This patch applies the
same fallback strategy to fixture discovery and to fetch_matches(), so the main
runtime can use SportLogic after the smoke test proves broad games work.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_query_guard_v2"


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


async def _load_fixtures_with_fallback(provider: Any, dates: list[str], stats: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    import httpx

    fixtures: list[dict[str, Any]] = []
    per_page = max(5, int(float(os.getenv("SPORTLOGIC_SMOKE_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE") or 100)))
    async with httpx.AsyncClient(timeout=provider.timeout, follow_redirects=True) as client:
        for date_key in dates:
            if not provider._budget_left():
                stats["budget_exhausted"] = True
                break
            day_rows: list[dict[str, Any]] = []
            for params in _param_variants_for_date(date_key, per_page=per_page):
                if not provider._budget_left():
                    stats["budget_exhausted"] = True
                    break
                payload = await provider._get_json(client, "/games", params, stats, preview)
                rows = provider._extract_list(payload)
                if rows:
                    day_rows.extend(rows)
                    preview.setdefault("query_variants_used", []).append({"scope": "dated", "date": date_key, "params": params, "rows": len(rows)})
                    break
            fixtures.extend(day_rows)
        if not fixtures and _truthy(os.getenv("SPORTLOGIC_BROAD_FALLBACK_ENABLED"), True):
            for params in _broad_param_variants(per_page=per_page):
                if not provider._budget_left():
                    stats["budget_exhausted"] = True
                    break
                payload = await provider._get_json(client, "/games", params, stats, preview)
                rows = provider._extract_list(payload)
                if rows:
                    fixtures.extend(rows)
                    preview.setdefault("query_variants_used", []).append({"scope": "broad", "date": "broad", "params": params, "rows": len(rows)})
                    break
    return _dedupe_rows(fixtures, provider)


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
    original_fetch_matches = getattr(cls, "fetch_matches", None)
    if not callable(original_load) or not callable(original_fetch_matches):
        return False

    async def load_fixtures_for_matches_patched(self: Any, matches: list[Any]):
        stats = self._stats("fixtures")
        preview: dict[str, Any] = {"sample_fixtures": [], "errors": [], "query_variants_used": []}
        dates = sorted({m.commence_time.astimezone(UTC).date().isoformat() for m in matches or []})[:6]
        fixtures = await _load_fixtures_with_fallback(self, dates, stats, preview)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["sportlogic_query_guard_enabled"] = True
        stats["query_variants_used"] = preview.get("query_variants_used", [])[:8]
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    async def fetch_matches_patched(self: Any):
        stats = self._stats("matches")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": [], "query_variants_used": []}
        if not self._ready(stats):
            return [], stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 3) or 3))
        dates = [(now + timedelta(days=offset)).date().isoformat() for offset in range(days_ahead + 1)]
        fixtures = await _load_fixtures_with_fallback(self, dates, stats, preview)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["sportlogic_query_guard_enabled"] = True
        stats["query_variants_used"] = preview.get("query_variants_used", [])[:8]
        preview["sample_fixtures"] = fixtures[:3]

        matches: list[Any] = []
        seen: set[str] = set()
        horizon = now + timedelta(days=days_ahead)
        for row in fixtures:
            match = self._row_to_match(row)
            if match is None:
                stats["fixtures_skipped"] += 1
                continue
            commence = match.commence_time.astimezone(UTC)
            if commence < now - timedelta(hours=6) or commence > horizon + timedelta(days=14):
                # Broad SportLogic responses can include a wider window. Keep a little
                # slack, but do not let stale/far fixtures flood the runner.
                stats["fixtures_skipped_window"] = int(stats.get("fixtures_skipped_window") or 0) + 1
                continue
            if match.match_key in seen:
                continue
            seen.add(match.match_key)
            matches.append(match)

        matches = self._prioritize_matches(matches)[: self.match_limit]
        stats["matches_built"] = len(matches)
        preview["sample_matches"] = [
            {
                "match_key": item.match_key,
                "league_name": item.league_name,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "commence_time": item.commence_time.isoformat(),
            }
            for item in matches[:8]
        ]
        return matches, stats, preview

    cls._load_fixtures_for_matches = load_fixtures_for_matches_patched
    cls.fetch_matches = fetch_matches_patched
    setattr(cls, PATCH_MARKER, True)
    return True
