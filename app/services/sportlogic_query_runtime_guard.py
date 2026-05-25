from __future__ import annotations

"""SportLogic request guard.

The SportLogic API documentation uses ``date_from`` / ``date_to`` on
``GET /api/v1/games``.  Earlier fallback probing accepted the first non-empty
response, including responses where the provider silently ignored the date
filter and returned stale rows.  That made the run report show
``fixtures_fetched > 0`` but ``matched = 0`` and no odds requests.

This guard now treats the date filter as a contract: a dated response is usable
only when at least one returned fixture actually falls inside the requested UTC
calendar day.  Stale/default rows are logged and skipped instead of poisoning the
SportLogic fixture cache.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_query_guard_v3"


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


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


def _row_date(provider: Any, row: dict[str, Any]) -> str:
    try:
        dt = provider._fixture_datetime(row)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).date().isoformat()
    except Exception:
        return ""
    return ""


def _window_stats(provider: Any, rows: list[dict[str, Any]], date_key: str) -> dict[str, Any]:
    sample_dates: list[str] = []
    inside = 0
    parsed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_date = _row_date(provider, row)
        if row_date:
            parsed += 1
            if row_date not in sample_dates and len(sample_dates) < 8:
                sample_dates.append(row_date)
            if row_date == date_key:
                inside += 1
    total = len(rows)
    outside = max(0, total - inside)
    return {
        "rows": total,
        "parsed_dates": parsed,
        "inside_window": inside,
        "outside_window": outside,
        "inside_ratio": round(inside / total, 4) if total else 0.0,
        "sample_dates": sample_dates,
        "date_filter_effective": bool(inside > 0),
    }


def _accept_dated_rows(provider: Any, rows: list[dict[str, Any]], date_key: str, params: dict[str, Any], stats: dict[str, Any], preview: dict[str, Any]) -> bool:
    diag = _window_stats(provider, rows, date_key)
    diag["scope"] = "dated"
    diag["date"] = date_key
    diag["params"] = params
    preview.setdefault("query_variants_used", []).append(diag)
    stats.setdefault("dated_query_diagnostics", []).append(diag)
    if not rows:
        return False
    if diag["inside_window"] <= 0:
        stats["stale_or_ignored_date_filter_rows"] = _to_int(stats.get("stale_or_ignored_date_filter_rows"), 0) + len(rows)
        stats["date_filter_ignored"] = True
        return False
    stats["date_filter_effective"] = True
    return True


def _param_variants_for_date(date_key: str, per_page: int = 100) -> list[dict[str, Any]]:
    # Use documented params first.  Non-documented aliases are only probes and
    # must pass the inside-window test before being accepted.
    return [
        {"status": "scheduled", "date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"status": "live", "date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"status": "pending", "date_from": date_key, "date_to": date_key, "per_page": per_page},
        {"date": date_key, "per_page": per_page},
        {"starts_at_from": f"{date_key}T00:00:00Z", "starts_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
        {"from": date_key, "to": date_key, "per_page": per_page},
        {"start_date": date_key, "end_date": date_key, "per_page": per_page},
    ]


def _broad_param_variants(per_page: int = 100) -> list[dict[str, Any]]:
    return [
        {"status": "scheduled", "per_page": per_page},
        {"per_page": per_page},
        {"status": "pending", "per_page": per_page},
        {"sport": "football", "per_page": per_page},
        {"sport": "soccer", "per_page": per_page},
    ]


def _filter_rows_to_requested_dates(provider: Any, rows: list[dict[str, Any]], dates: set[str]) -> list[dict[str, Any]]:
    if not dates:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        row_date = _row_date(provider, row)
        if row_date in dates:
            filtered.append(row)
    return filtered


async def _load_fixtures_with_fallback(provider: Any, dates: list[str], stats: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    import httpx

    fixtures: list[dict[str, Any]] = []
    requested_dates = set(dates or [])
    per_page = max(5, int(float(os.getenv("SPORTLOGIC_SMOKE_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE") or 100)))
    async with httpx.AsyncClient(timeout=provider.timeout, follow_redirects=True) as client:
        # SportLogic docs support date_from/date_to on /games, so use one
        # cursor-paginated window request first.  The old per-date probe loop
        # easily hit the 10 RPM free limit and caused 429 before useful odds work.
        if dates:
            window_params = {"date_from": min(dates), "date_to": max(dates), "status": "scheduled", "per_page": per_page}
            rows = await provider._get_paginated_list(client, "/games", window_params, stats, preview, max_pages=getattr(provider, "max_pages", 3))
            filtered = _filter_rows_to_requested_dates(provider, rows, requested_dates)
            preview.setdefault("query_variants_used", []).append({
                "scope": "window",
                "date": "window",
                "params": window_params,
                "rows": len(rows),
                "rows_inside_requested_dates": len(filtered),
                "sample_dates": sorted({_row_date(provider, row) for row in rows if _row_date(provider, row)})[:8],
                "date_filter_effective": bool(filtered),
            })
            if filtered:
                stats["date_filter_effective"] = True
                fixtures.extend(filtered)
            elif rows:
                stats["stale_or_ignored_date_filter_rows"] = _to_int(stats.get("stale_or_ignored_date_filter_rows"), 0) + len(rows)
                stats["date_filter_ignored"] = True

        if not fixtures and dates and provider._budget_left():
            window_params = {"date_from": min(dates), "date_to": max(dates), "per_page": per_page}
            rows = await provider._get_paginated_list(client, "/games", window_params, stats, preview, max_pages=getattr(provider, "max_pages", 3))
            filtered = _filter_rows_to_requested_dates(provider, rows, requested_dates)
            preview.setdefault("query_variants_used", []).append({
                "scope": "window_no_status",
                "date": "window",
                "params": window_params,
                "rows": len(rows),
                "rows_inside_requested_dates": len(filtered),
                "sample_dates": sorted({_row_date(provider, row) for row in rows if _row_date(provider, row)})[:8],
                "date_filter_effective": bool(filtered),
            })
            if filtered:
                stats["date_filter_effective"] = True
                fixtures.extend(filtered)

        if not fixtures and _truthy(os.getenv("SPORTLOGIC_BROAD_FALLBACK_ENABLED"), True):
            for params in _broad_param_variants(per_page=per_page):
                if not provider._budget_left():
                    stats["budget_exhausted"] = True
                    break
                payload = await provider._get_json(client, "/games", params, stats, preview)
                rows = provider._extract_list(payload)
                if not rows:
                    continue
                filtered = _filter_rows_to_requested_dates(provider, rows, requested_dates)
                preview.setdefault("query_variants_used", []).append({
                    "scope": "broad",
                    "date": "broad",
                    "params": params,
                    "rows": len(rows),
                    "rows_inside_requested_dates": len(filtered),
                    "sample_dates": sorted({_row_date(provider, row) for row in rows if _row_date(provider, row)})[:8],
                })
                if filtered:
                    fixtures.extend(filtered)
                    stats["broad_fallback_rows_filtered_to_requested_dates"] = len(filtered)
                    break
                stats["broad_fallback_stale_rows"] = _to_int(stats.get("broad_fallback_stale_rows"), 0) + len(rows)
    deduped = _dedupe_rows(fixtures, provider)
    if not deduped and _to_int(stats.get("stale_or_ignored_date_filter_rows"), 0) > 0:
        stats["diagnosis"] = "games_endpoint_returned_unmatched_or_stale_rows"
    elif deduped:
        stats["diagnosis"] = "date_filter_effective"
    return deduped


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
        stats["query_variants_used"] = preview.get("query_variants_used", [])[:12]
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
        stats["query_variants_used"] = preview.get("query_variants_used", [])[:12]
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
            if commence < now - timedelta(hours=6) or commence > horizon + timedelta(hours=12):
                stats["fixtures_skipped_window"] = _to_int(stats.get("fixtures_skipped_window"), 0) + 1
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
