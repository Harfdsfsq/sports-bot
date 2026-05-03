from __future__ import annotations

"""SportLogic fixture discovery patch.

The SportLogic /games endpoint appears to treat date_to as an exclusive upper
bound. Requests such as date_from=2026-05-03&date_to=2026-05-03 can return an
empty list even when the same day has games. This patch widens the date range
and tries a small set of compatible parameter shapes before odds matching.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v1"


def _date_key(value: datetime) -> str:
    return value.astimezone(UTC).date().isoformat()


def _safe_event_key(provider: Any, row: dict[str, Any]) -> str:
    try:
        event_id = provider._event_id(row)
    except Exception:
        event_id = ""
    if event_id:
        return f"id:{event_id}"
    home = ""
    away = ""
    start = ""
    try:
        home = provider._team_name(row, "home") or ""
        away = provider._team_name(row, "away") or ""
        dt = provider._fixture_datetime(row)
        start = dt.isoformat() if dt is not None else ""
    except Exception:
        pass
    return f"fallback:{home}|{away}|{start}"


def _dedupe_rows(provider: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _safe_event_key(provider, row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _fixture_query_variants(day: datetime) -> list[dict[str, Any]]:
    date_from = day.date().isoformat()
    date_to_next = (day.date() + timedelta(days=1)).isoformat()
    compact = day.strftime("%Y%m%d")
    slash = day.strftime("%Y/%m/%d")
    dot = day.strftime("%d.%m.%Y")
    return [
        {"date_from": date_from, "date_to": date_to_next, "per_page": 100},
        {"date": date_from, "per_page": 100},
        {"day": date_from, "per_page": 100},
        {"from": date_from, "to": date_to_next, "per_page": 100},
        {"start_date": date_from, "end_date": date_to_next, "per_page": 100},
        {"date": compact, "per_page": 100},
        {"date": slash, "per_page": 100},
        {"date": dot, "per_page": 100},
    ]


def _collect_candidate_dates(matches: list[Any]) -> list[datetime]:
    dates: list[datetime] = []
    seen: set[str] = set()
    now = datetime.now(UTC)
    for match in matches:
        try:
            base = match.commence_time.astimezone(UTC)
        except Exception:
            continue
        for offset in (-1, 0, 1):
            day = base + timedelta(days=offset)
            key = day.date().isoformat()
            if key not in seen:
                seen.add(key)
                dates.append(day)
    if not dates:
        for offset in (0, 1):
            day = now + timedelta(days=offset)
            key = day.date().isoformat()
            if key not in seen:
                seen.add(key)
                dates.append(day)
    return sorted(dates, key=lambda item: item.date())


def install() -> bool:
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    async def load_fixtures_for_matches_patched(self: Any, matches: list[Any]):
        stats = self._stats("fixtures")
        preview: dict[str, Any] = {"sample_fixtures": [], "errors": [], "fixture_query_attempts": []}
        dates = _collect_candidate_dates(matches)[:8]
        fixtures: list[dict[str, Any]] = []
        rows_by_date: dict[str, int] = {}
        empty_attempts = 0

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for day in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_key = _date_key(day)
                day_rows_before = len(fixtures)
                variants = _fixture_query_variants(day)
                for params in variants:
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await self._get_json(client, "/games", params, stats, preview)
                    rows = self._extract_list(payload)
                    attempt = {
                        "date": day_key,
                        "params": dict(params),
                        "rows": len(rows),
                    }
                    if len(preview["fixture_query_attempts"]) < 16:
                        preview["fixture_query_attempts"].append(attempt)
                    stats.setdefault("fixture_query_attempts", []).append(attempt)
                    if rows:
                        fixtures.extend(rows)
                        # One working shape per date is enough; avoid wasting budget.
                        break
                    empty_attempts += 1
                rows_by_date[day_key] = len(fixtures) - day_rows_before

        fixtures = _dedupe_rows(self, fixtures)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["games_fetched"] = len(fixtures)
        stats["fixture_dates_requested"] = [_date_key(day) for day in dates]
        stats["fixture_rows_by_date"] = rows_by_date
        stats["empty_fixture_attempts"] = empty_attempts
        if not fixtures and not stats.get("response_errors"):
            stats["empty_games_reason"] = "all_fixture_query_variants_empty"
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    async def fetch_matches_patched(self: Any):
        stats = self._stats("matches")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": []}
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

        matches_out = []
        seen = set()
        horizon = now + timedelta(days=days_ahead)
        for row in fixtures:
            match = self._row_to_match(row)
            if match is None:
                stats["fixtures_skipped"] += 1
                continue
            commence = match.commence_time.astimezone(UTC)
            if commence < now - timedelta(hours=2) or commence > horizon:
                continue
            if match.match_key in seen:
                continue
            seen.add(match.match_key)
            matches_out.append(match)
        matches_out = self._prioritize_matches(matches_out)[: self.match_limit]
        stats["matches_built"] = len(matches_out)
        preview["sample_matches"] = [
            {
                "match_key": item.match_key,
                "league_name": item.league_name,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "commence_time": item.commence_time.isoformat(),
            }
            for item in matches_out[:8]
        ]
        return matches_out, stats, preview

    cls._load_fixtures_for_matches = load_fixtures_for_matches_patched
    cls.fetch_matches = fetch_matches_patched
    setattr(cls, PATCH_MARKER, True)
    return True
