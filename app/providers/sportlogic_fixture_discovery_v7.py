from __future__ import annotations

"""SportLogic fixture discovery v7: documented cursor pagination.

Docs say `/games` is ordered by start_time desc and list endpoints use cursor
pagination. The previous probes used `page`, which SportLogic can ignore. This
module reuses the v6 row parser but replaces discovery with:

  /games?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&status=scheduled&per_page=100
  /games?date_from=YYYY-MM-DD&status=scheduled&per_page=100
  next pages via cursor=meta.next_cursor or pagination.next_cursor.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.providers.sportlogic_fixture_discovery_v6 import (  # parser helpers
    _collect_candidate_dates,
    _date_key,
    _dedupe_rows,
    _event_id,
    _fixture_datetime,
    _league_name,
    _row_to_match,
    _safe_event_key,
    _sample_rows,
    _target_window,
    _team_name,
)

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v7_cursor_pagination"


def _dig(payload: Any, *path: str) -> Any:
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "response", "results", "items", "games", "fixtures", "matches", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _extract_list(value)
                if nested:
                    return nested
    return []


def _next_cursor(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for path in (("pagination", "next_cursor"), ("meta", "next_cursor"), ("data", "pagination", "next_cursor"), ("links", "next_cursor")):
        value = _dig(payload, *path)
        if value not in (None, ""):
            return str(value)
    return ""


def _has_more(payload: Any) -> bool | None:
    if not isinstance(payload, dict):
        return None
    for path in (("pagination", "has_more"), ("meta", "has_more"), ("data", "pagination", "has_more")):
        value = _dig(payload, *path)
        if isinstance(value, bool):
            return value
    return None


def _row_in_window(row: dict[str, Any], window_start: datetime, window_end: datetime) -> bool:
    dt = _fixture_datetime(row)
    return dt is not None and window_start <= dt.astimezone(UTC) <= window_end


def _cursor_scan_max() -> int:
    try:
        return max(1, min(12, int(float(os.getenv("SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX", "6") or 6))))
    except Exception:
        return 6


def _param_sets(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "status": "scheduled", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "status": "scheduled", "per_page": 100},
    ]


def _page_time_span(rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = [dt for row in rows if (dt := _fixture_datetime(row)) is not None]
    if not times:
        return {"min_start": "", "max_start": "", "sample_start_times": []}
    times = sorted(times)
    return {
        "min_start": times[0].isoformat(),
        "max_start": times[-1].isoformat(),
        "sample_start_times": [item.isoformat() for item in times[:3]],
    }


def _cursor_preview(cursor: str) -> str:
    if not cursor:
        return ""
    return cursor[:24] + ("..." if len(cursor) > 24 else "")


def install() -> bool:
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    cls._team_name = staticmethod(_team_name)
    cls._fixture_datetime = staticmethod(_fixture_datetime)
    cls._league_name = staticmethod(_league_name)
    cls._event_id = staticmethod(_event_id)
    cls._row_to_match = staticmethod(_row_to_match)

    async def load_fixtures_for_matches_patched(self: Any, matches: list[Any]):
        stats = self._stats("fixtures")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_fixture_parse": [], "sample_fixture_parse_kept": [], "errors": [], "fixture_query_attempts": []}
        window_start, window_end = _target_window(matches)
        dates = [day for day in _collect_candidate_dates(matches) if day.date() >= window_start.date()][:6]
        fixtures: list[dict[str, Any]] = []
        rows_by_date: dict[str, int] = {}
        stale_rows = 0
        empty_attempts = 0
        cursor_scan_max = _cursor_scan_max()

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for day in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_key = _date_key(day)
                before = len(fixtures)
                for param_index, base_params in enumerate(_param_sets(day)):
                    if param_index > 0 and len(fixtures) > before:
                        break
                    cursor = ""
                    seen_cursors: set[str] = set()
                    for cursor_page in range(1, cursor_scan_max + 1):
                        if not self._budget_left():
                            stats["budget_exhausted"] = True
                            break
                        params = dict(base_params)
                        if cursor:
                            params["cursor"] = cursor
                        payload = await self._get_json(client, "/games", params, stats, preview)
                        rows = _extract_list(payload)
                        kept = [row for row in rows if _row_in_window(row, window_start, window_end)]
                        stale_rows += max(0, len(rows) - len(kept))
                        next_cursor = _next_cursor(payload)
                        has_more = _has_more(payload)
                        attempt = {
                            "date": day_key,
                            "params": {k: ("<cursor>" if k == "cursor" else v) for k, v in params.items()},
                            "cursor_page": cursor_page,
                            "rows": len(rows),
                            "kept": len(kept),
                            "next_cursor": _cursor_preview(next_cursor),
                            "has_more": has_more,
                            **_page_time_span(rows),
                        }
                        if len(preview["fixture_query_attempts"]) < 40:
                            preview["fixture_query_attempts"].append(attempt)
                        stats.setdefault("fixture_query_attempts", []).append(attempt)
                        if rows and not preview["sample_fixture_parse"]:
                            preview["sample_fixture_parse"] = _sample_rows(rows, 3)
                            stats["sample_fixture_keys"] = [list(row.keys())[:40] for row in rows[:3] if isinstance(row, dict)]
                        if kept:
                            fixtures.extend(kept)
                            preview["sample_fixture_parse_kept"] = _sample_rows(kept, 3)
                        if not rows:
                            empty_attempts += 1
                            break
                        if not next_cursor or next_cursor in seen_cursors or has_more is False:
                            break
                        seen_cursors.add(next_cursor)
                        cursor = next_cursor
                rows_by_date[day_key] = len(fixtures) - before
                if len(fixtures) >= max(8, int(getattr(self, "match_limit", 40) or 40) // 2):
                    break

        fixtures = _dedupe_rows(fixtures)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["games_fetched"] = len(fixtures)
        stats["fixture_dates_requested"] = [_date_key(day) for day in dates]
        stats["fixture_rows_by_date"] = rows_by_date
        stats["empty_fixture_attempts"] = empty_attempts
        stats["fixture_stale_rows_filtered"] = stale_rows
        stats["fixture_window_start"] = window_start.isoformat()
        stats["fixture_window_end"] = window_end.isoformat()
        stats["fixture_cursor_scan_max"] = cursor_scan_max
        preview["sample_fixtures"] = fixtures[:3]
        if fixtures and not preview["sample_fixture_parse_kept"]:
            preview["sample_fixture_parse_kept"] = _sample_rows(fixtures, 3)
        if not fixtures and not stats.get("response_errors"):
            stats["empty_games_reason"] = "cursor_scan_found_no_rows_inside_target_window"
        return fixtures, stats, preview

    async def fetch_matches_patched(self: Any):
        stats = self._stats("matches")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_fixture_parse": [], "sample_fixture_parse_kept": [], "sample_matches": [], "errors": []}
        if not self._ready(stats):
            return [], stats, preview
        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 3) or 3))

        class _ProbeMatch:
            def __init__(self, commence_time: datetime) -> None:
                self.commence_time = commence_time

        probe_matches = [_ProbeMatch(now + timedelta(days=offset)) for offset in range(days_ahead + 1)]
        fixtures, fixture_stats, fixture_preview = await load_fixtures_for_matches_patched(self, probe_matches)
        self._merge_stats(stats, fixture_stats)
        preview["sample_fixtures"] = fixture_preview.get("sample_fixtures", [])[:3]
        preview["sample_fixture_parse"] = fixture_preview.get("sample_fixture_parse", [])[:3]
        preview["sample_fixture_parse_kept"] = fixture_preview.get("sample_fixture_parse_kept", [])[:3]

        matches_out = []
        seen = set()
        horizon = now + timedelta(days=days_ahead + 2)
        parse_rejects: dict[str, int] = {}
        out_of_window = 0
        for row in fixtures:
            match = _row_to_match(row)
            if match is None:
                reason = "missing_start_time" if _fixture_datetime(row) is None else "missing_team"
                parse_rejects[reason] = parse_rejects.get(reason, 0) + 1
                stats["fixtures_skipped"] += 1
                continue
            commence = match.commence_time.astimezone(UTC)
            if commence < now - timedelta(hours=6) or commence > horizon:
                out_of_window += 1
                continue
            if match.match_key in seen:
                continue
            seen.add(match.match_key)
            matches_out.append(match)
        matches_out = self._prioritize_matches(matches_out)[: self.match_limit]
        stats["matches_built"] = len(matches_out)
        stats["fixture_parse_rejects"] = parse_rejects
        stats["fixture_out_of_window"] = out_of_window
        preview["sample_matches"] = [
            {"match_key": item.match_key, "league_name": item.league_name, "home_team": item.home_team, "away_team": item.away_team, "commence_time": item.commence_time.isoformat()}
            for item in matches_out[:8]
        ]
        return matches_out, stats, preview

    cls._load_fixtures_for_matches = load_fixtures_for_matches_patched
    cls.fetch_matches = fetch_matches_patched
    setattr(cls, PATCH_MARKER, True)
    return True
