from __future__ import annotations

"""SportLogic fixture discovery v6.

The API has proven to return real rows with the native shape:
  id, league_id, home_team, away_team, start_time, ...

The remaining issue is discovery: several date-like query params are ignored or
return the same early rows. Therefore v6 avoids burning budget on many query
shapes and instead scans deeper pages for the working `date=YYYY-MM-DD` shape,
recording min/max start_time per page so the next bottleneck is obvious.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v6_deep_date_scan"

TEAM_NAME_KEYS = ("name", "team_name", "teamName", "display_name", "displayName", "full_name", "fullName", "short_name", "shortName", "title", "label", "value")
HOME_KEYS = ("home", "home_team", "homeTeam", "home_team_name", "homeTeamName", "home_name", "homeName", "team_home", "teamHome", "localteam", "local_team", "localTeam", "host", "team1", "competitor1", "participant1")
AWAY_KEYS = ("away", "away_team", "awayTeam", "away_team_name", "awayTeamName", "away_name", "awayName", "team_away", "teamAway", "visitorteam", "visitor_team", "visitorTeam", "guest", "team2", "competitor2", "participant2")
LEAGUE_KEYS = ("league", "competition", "tournament", "championship", "season", "category", "country")
DATE_KEYS = ("commence_time", "start_time", "startTime", "starts_at", "startsAt", "start_at", "startAt", "scheduled", "scheduled_at", "scheduledAt", "kickoff", "kickoff_time", "kickoffTime", "event_time", "eventTime", "match_time", "matchTime", "datetime", "date_time", "dateTime", "utcDate", "timestamp", "time", "date", "game_date", "gameDate")
ID_KEYS = ("id", "game_id", "gameId", "event_id", "eventId", "fixture_id", "fixtureId", "match_id", "matchId", "uuid", "slug")


def _date_key(value: datetime) -> str:
    return value.astimezone(UTC).date().isoformat()


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in TEAM_NAME_KEYS:
            item = value.get(key)
            if item not in (None, ""):
                return _text(item)
    return ""


def _dig(payload: Any, *path: str) -> Any:
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            text = _text(payload.get(key))
            if text:
                return text
    return ""


def _team_from_side_lists(row: dict[str, Any], side: str) -> str:
    side_tokens = {"home", "h", "1", "local", "host"} if side == "home" else {"away", "a", "2", "visitor", "guest"}
    for container_key in ("participants", "competitors", "teams", "contestants", "players"):
        value = row.get(container_key)
        if isinstance(value, dict):
            direct = value.get(side) or value.get("local" if side == "home" else "visitor") or value.get("team1" if side == "home" else "team2")
            text = _text(direct)
            if text:
                return text
            value = list(value.values())
        if not isinstance(value, list):
            continue
        fallback_by_order: list[Any] = []
        for item in value:
            if not isinstance(item, dict):
                fallback_by_order.append(item)
                continue
            fallback_by_order.append(item)
            marker_text = {str(item.get(key) or "").strip().lower() for key in ("side", "home_away", "homeAway", "qualifier", "position", "type", "role") if item.get(key) not in (None, "")}
            if marker_text & side_tokens:
                text = _text(item)
                if text:
                    return text
        if len(fallback_by_order) >= 2:
            text = _text(fallback_by_order[0 if side == "home" else 1])
            if text:
                return text
    return ""


def _team_name(row: dict[str, Any], side: str) -> str:
    keys = HOME_KEYS if side == "home" else AWAY_KEYS
    text = _first_text(row, keys)
    if text:
        return text
    paths = (("fixture", "home"), ("fixture", "homeTeam"), ("game", "home"), ("game", "homeTeam"), ("event", "home"), ("event", "homeTeam"), ("match", "home"), ("match", "homeTeam")) if side == "home" else (("fixture", "away"), ("fixture", "awayTeam"), ("game", "away"), ("game", "awayTeam"), ("event", "away"), ("event", "awayTeam"), ("match", "away"), ("match", "awayTeam"))
    for path in paths:
        text = _text(_dig(row, *path))
        if text:
            return text
    return _team_from_side_lists(row, side)


def _league_name(row: dict[str, Any]) -> str:
    for key in LEAGUE_KEYS:
        text = _text(row.get(key))
        if text:
            return text
    for key in ("league_name", "leagueName", "competition_name", "competitionName", "tournament_name", "tournamentName", "sport_title", "sportTitle"):
        text = _text(row.get(key))
        if text:
            return text
    for path in (("fixture", "league"), ("game", "league"), ("event", "league"), ("match", "league")):
        text = _text(_dig(row, *path))
        if text:
            return text
    return "SportLogic"


def _event_id(row: dict[str, Any]) -> str:
    text = _first_text(row, ID_KEYS)
    if text:
        return text
    for path in (("fixture", "id"), ("game", "id"), ("event", "id"), ("match", "id")):
        text = _text(_dig(row, *path))
        if text:
            return text
    return ""


def _parse_datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        try:
            number = float(value)
            if number > 1_000_000_000_000:
                return datetime.fromtimestamp(number / 1000.0, tz=UTC)
            if number > 1_000_000_000:
                return datetime.fromtimestamp(number, tz=UTC)
        except Exception:
            return None
    if isinstance(value, dict):
        for key in DATE_KEYS:
            dt = _parse_datetime_value(value.get(key))
            if dt is not None:
                return dt
        date_part = value.get("date") or value.get("day")
        time_part = value.get("time") or value.get("hour") or value.get("kickoff")
        if date_part and time_part:
            return _parse_datetime_value(f"{date_part} {time_part}")
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit() and len(raw) in {10, 13}:
        return _parse_datetime_value(int(raw))
    normalized = raw.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(normalized.replace(" ", "T", 1) if " " in normalized and "T" not in normalized else normalized)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def _fixture_datetime(row: dict[str, Any]) -> datetime | None:
    for key in DATE_KEYS:
        dt = _parse_datetime_value(row.get(key))
        if dt is not None:
            return dt.astimezone(UTC)
    for path in (("fixture", "date"), ("fixture", "start_time"), ("game", "date"), ("game", "start_time"), ("event", "date"), ("match", "date")):
        dt = _parse_datetime_value(_dig(row, *path))
        if dt is not None:
            return dt.astimezone(UTC)
    return None


def _is_low_tier_text(text: str) -> bool:
    value = str(text or "").lower()
    return any(token in value for token in ("u19", "u20", "u21", "u23", "reserve", "reserves", "women", "amateur", "youth", " 2", " ii"))


def _row_to_match(row: dict[str, Any]) -> Any | None:
    try:
        from app.schemas import Match
    except Exception:
        return None
    home = _team_name(row, "home") if isinstance(row, dict) else ""
    away = _team_name(row, "away") if isinstance(row, dict) else ""
    commence = _fixture_datetime(row) if isinstance(row, dict) else None
    if not home or not away or commence is None:
        return None
    league = _league_name(row)
    return Match(
        source="sportlogic",
        source_event_id=_event_id(row),
        sport_key="soccer",
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=commence,
        home_team_norm="",
        away_team_norm="",
        league_key=str(_dig(row, "league", "id") or row.get("league_id") or ""),
        tier="low" if _is_low_tier_text(league) else "mid",
        metadata={"sportlogic_fixture": row},
    )


def _safe_event_key(row: dict[str, Any]) -> str:
    event_id = _event_id(row)
    if event_id:
        return f"id:{event_id}"
    return f"fallback:{_team_name(row, 'home')}|{_team_name(row, 'away')}|{_fixture_datetime(row)}"


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _safe_event_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _append_date(out: list[datetime], seen: set[str], day: datetime) -> None:
    key = day.date().isoformat()
    if key not in seen:
        seen.add(key)
        out.append(day)


def _collect_candidate_dates(matches: list[Any]) -> list[datetime]:
    now = datetime.now(UTC)
    out: list[datetime] = []
    seen: set[str] = set()
    starts: list[datetime] = []
    for match in matches:
        try:
            starts.append(match.commence_time.astimezone(UTC))
        except Exception:
            pass
    if starts:
        for start in sorted(starts):
            _append_date(out, seen, start)
        for start in sorted(starts):
            _append_date(out, seen, start + timedelta(days=1))
        for start in sorted(starts):
            _append_date(out, seen, start - timedelta(days=1))
    else:
        for offset in range(0, 4):
            _append_date(out, seen, now + timedelta(days=offset))
    return out


def _target_window(matches: list[Any], now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    starts: list[datetime] = []
    for match in matches:
        try:
            starts.append(match.commence_time.astimezone(UTC))
        except Exception:
            pass
    if starts:
        # Smoke should verify SportLogic can build matches for the same broad inventory,
        # not only the tiny publish window.
        return min(starts) - timedelta(hours=12), max(starts) + timedelta(days=2)
    return now - timedelta(hours=6), now + timedelta(days=4)


def _row_in_window(row: dict[str, Any], window_start: datetime, window_end: datetime) -> bool:
    dt = _fixture_datetime(row)
    return dt is not None and window_start <= dt.astimezone(UTC) <= window_end


def _sample_rows(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for row in rows[:limit]:
        dt = _fixture_datetime(row)
        sample.append({
            "keys": list(row.keys())[:40],
            "event_id": _event_id(row),
            "home": _team_name(row, "home"),
            "away": _team_name(row, "away"),
            "league": _league_name(row),
            "commence_time": dt.isoformat() if dt is not None else "",
            "raw": {key: row.get(key) for key in list(row.keys())[:16]},
        })
    return sample


def _page_scan_max() -> int:
    try:
        return max(1, min(25, int(float(os.getenv("SPORTLOGIC_FIXTURE_PAGE_SCAN_MAX", "16") or 16))))
    except Exception:
        return 16


def _priority_param_sets(day: datetime) -> list[dict[str, Any]]:
    date_key = day.date().isoformat()
    next_key = (day.date() + timedelta(days=1)).isoformat()
    return [
        {"date": date_key, "per_page": 100},
        {"start_time_from": date_key, "start_time_to": next_key, "per_page": 100},
        {"starts_after": f"{date_key}T00:00:00Z", "starts_before": f"{next_key}T00:00:00Z", "per_page": 100},
        {"date_from": date_key, "date_to": next_key, "per_page": 100},
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
        stale_rows = 0
        empty_attempts = 0
        page_scan_max = _page_scan_max()
        rows_by_date: dict[str, int] = {}

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for day in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_key = _date_key(day)
                before_count = len(fixtures)
                # Do not try every weak alias first. Scan the working `date` shape deeply,
                # then try explicit start_time aliases if needed.
                for param_index, base_params in enumerate(_priority_param_sets(day)):
                    if param_index > 0 and len(fixtures) > before_count:
                        break
                    max_pages = page_scan_max if param_index == 0 else 2
                    for page in range(1, max_pages + 1):
                        if not self._budget_left():
                            stats["budget_exhausted"] = True
                            break
                        params = dict(base_params)
                        params["page"] = page
                        payload = await self._get_json(client, "/games", params, stats, preview)
                        rows = self._extract_list(payload)
                        kept = [row for row in rows if _row_in_window(row, window_start, window_end)]
                        stale_rows += max(0, len(rows) - len(kept))
                        span = _page_time_span(rows)
                        attempt = {"date": day_key, "params": dict(params), "rows": len(rows), "kept": len(kept), **span}
                        if len(preview["fixture_query_attempts"]) < 36:
                            preview["fixture_query_attempts"].append(attempt)
                        stats.setdefault("fixture_query_attempts", []).append(attempt)
                        if rows and not preview["sample_fixture_parse"]:
                            preview["sample_fixture_parse"] = _sample_rows(rows, 3)
                            stats["sample_fixture_keys"] = [list(row.keys())[:40] for row in rows[:3] if isinstance(row, dict)]
                        if kept:
                            fixtures.extend(kept)
                            preview["sample_fixture_parse_kept"] = _sample_rows(kept, 3)
                            break
                        if not rows:
                            empty_attempts += 1
                            break
                        if len(rows) < int(params.get("per_page") or 100):
                            break
                rows_by_date[day_key] = len(fixtures) - before_count
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
        stats["fixture_page_scan_max"] = page_scan_max
        preview["sample_fixtures"] = fixtures[:3]
        if fixtures and not preview["sample_fixture_parse_kept"]:
            preview["sample_fixture_parse_kept"] = _sample_rows(fixtures, 3)
        if not fixtures and not stats.get("response_errors"):
            stats["empty_games_reason"] = "deep_date_scan_found_no_rows_inside_target_window"
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
