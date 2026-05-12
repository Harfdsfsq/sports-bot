from __future__ import annotations

"""Runtime fixups for the core source coverage patch.

This module intentionally contains only small, defensive monkey patches for
provider/report edge cases discovered from live run logs:
- SStats v1 result rows use homeResult/awayResult/homeFTResult/awayFTResult.
- The SportLogic query guard must not accept stale rows from fallback params.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_INSTALLED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _parse_start(provider: Any, row: dict[str, Any]) -> datetime | None:
    for key in (
        "date", "Date", "event_date", "start", "Start", "start_time", "startTime",
        "start_at", "startAt", "gameDate", "GameDate", "kickoff", "Kickoff",
    ):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            from app.utils import parse_datetime
            return parse_datetime(value).astimezone(UTC)
        except Exception:
            continue
    try:
        dt = provider._fixture_datetime(row)
        return dt.astimezone(UTC) if dt is not None else None
    except Exception:
        return None


def _filter_rows_for_date(provider: Any, rows: list[dict[str, Any]], date_key: str, slack_hours: int = 3) -> list[dict[str, Any]]:
    target = datetime.fromisoformat(date_key).replace(tzinfo=UTC)
    start_min = target - timedelta(hours=slack_hours)
    start_max = target + timedelta(days=1, hours=slack_hours)
    filtered: list[dict[str, Any]] = []
    stale = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _parse_start(provider, row)
        if start is None:
            # Keep rows without a parsable date only for non-dated broad fallback.
            continue
        if start_min <= start <= start_max:
            filtered.append(row)
        else:
            stale += 1
    return filtered


def _patch_sstats_v1_goals() -> None:
    try:
        from app.providers.sstats_v1 import SStatsContextProvider
    except Exception:
        return
    if getattr(SStatsContextProvider, "_harizon_result_field_fix", False):
        return

    @staticmethod
    def _goals(row: dict[str, Any], side: str) -> int | None:
        home_keys = [
            "homeGoals", "HomeGoals", "homeScore", "HomeScore", "homeResult", "HomeResult",
            "homeFTResult", "HomeFTResult", "homeFullTimeResult", "HomeFullTimeResult",
            "Score1", "score1", "home",
        ]
        away_keys = [
            "awayGoals", "AwayGoals", "awayScore", "AwayScore", "awayResult", "AwayResult",
            "awayFTResult", "AwayFTResult", "awayFullTimeResult", "AwayFullTimeResult",
            "Score2", "score2", "away",
        ]
        for key in (home_keys if side == "home" else away_keys):
            value = row.get(key)
            try:
                if value not in (None, "") and not isinstance(value, dict):
                    return int(float(value))
            except Exception:
                continue
        score = row.get("score") or row.get("Score")
        if isinstance(score, dict):
            keys = ("home", "Home", "homeResult", "homeFT") if side == "home" else ("away", "Away", "awayResult", "awayFT")
            for key in keys:
                value = score.get(key)
                try:
                    if value not in (None, "") and not isinstance(value, dict):
                        return int(float(value))
                except Exception:
                    continue
        return None

    SStatsContextProvider._goals = _goals
    SStatsContextProvider._harizon_result_field_fix = True


def _patch_sportlogic_query_guard() -> None:
    try:
        import app.services.sportlogic_query_runtime_guard as guard
    except Exception:
        return
    if getattr(guard, "_HARIZON_STALE_FILTER_FIX", False):
        return

    def _param_variants_for_date(date_key: str, per_page: int = 100) -> list[dict[str, Any]]:
        # Documented/current keys first. Do not use legacy from/to as a successful
        # variant unless it returns rows inside the requested date after filtering.
        return [
            {"date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"status": "scheduled", "date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"status": "pending", "date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"starts_at_from": f"{date_key}T00:00:00Z", "starts_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
            {"start_at_from": f"{date_key}T00:00:00Z", "start_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
            {"date": date_key, "per_page": per_page},
            {"from": date_key, "to": date_key, "per_page": per_page},
        ]

    def _broad_param_variants(per_page: int = 100) -> list[dict[str, Any]]:
        return [
            {"status": "scheduled", "per_page": per_page},
            {"status": "pending", "per_page": per_page},
            {"sport": "football", "per_page": per_page},
            {"sport": "soccer", "per_page": per_page},
            {"per_page": per_page},
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
                    raw_rows = provider._extract_list(payload)
                    rows = _filter_rows_for_date(provider, raw_rows, date_key)
                    if raw_rows and not rows:
                        preview.setdefault("stale_query_variants_rejected", []).append({
                            "scope": "dated",
                            "date": date_key,
                            "params": params,
                            "raw_rows": len(raw_rows),
                            "kept_rows": 0,
                        })
                        continue
                    if rows:
                        day_rows.extend(rows)
                        preview.setdefault("query_variants_used", []).append({
                            "scope": "dated",
                            "date": date_key,
                            "params": params,
                            "rows": len(rows),
                            "raw_rows": len(raw_rows),
                        })
                        break
                fixtures.extend(day_rows)
            if not fixtures and _truthy(os.getenv("SPORTLOGIC_BROAD_FALLBACK_ENABLED"), False):
                today = datetime.now(UTC).date().isoformat()
                for params in _broad_param_variants(per_page=per_page):
                    if not provider._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await provider._get_json(client, "/games", params, stats, preview)
                    raw_rows = provider._extract_list(payload)
                    rows: list[dict[str, Any]] = []
                    for date_key in dates or [today]:
                        rows.extend(_filter_rows_for_date(provider, raw_rows, date_key, slack_hours=6))
                    if rows:
                        fixtures.extend(rows)
                        preview.setdefault("query_variants_used", []).append({"scope": "broad_filtered", "date": "broad", "params": params, "rows": len(rows), "raw_rows": len(raw_rows)})
                        break
                    if raw_rows:
                        preview.setdefault("stale_query_variants_rejected", []).append({"scope": "broad", "date": "broad", "params": params, "raw_rows": len(raw_rows), "kept_rows": 0})
        return guard._dedupe_rows(fixtures, provider)

    guard._param_variants_for_date = _param_variants_for_date
    guard._broad_param_variants = _broad_param_variants
    guard._load_fixtures_with_fallback = _load_fixtures_with_fallback
    guard._HARIZON_STALE_FILTER_FIX = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    _patch_sstats_v1_goals()
    _patch_sportlogic_query_guard()
    return {"status": "installed", "sstats_result_fields": True, "sportlogic_stale_filter": True}
