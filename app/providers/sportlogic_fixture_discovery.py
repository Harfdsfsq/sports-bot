from __future__ import annotations

"""SportLogic fixture discovery + fixture parser hardening.

SportLogic /games can return rows, but the first page is not guaranteed to be in
the bot's current publish window.  This module therefore does three things:

1. tries several /games query parameter shapes;
2. scans a bounded number of pages;
3. keeps only fixtures whose parsed start_time overlaps the target match window.

It also patches SportLogicProvider's row parser so fixtures with SportLogic's
native shape (home_team/away_team/start_time) become canonical Match objects.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

UTC = timezone.utc
PATCH_MARKER = "_harizon_sportlogic_fixture_discovery_v4_windowed_pages"

TEAM_NAME_KEYS = (
    "name",
    "team_name",
    "teamName",
    "display_name",
    "displayName",
    "full_name",
    "fullName",
    "short_name",
    "shortName",
    "title",
    "label",
    "value",
)

HOME_KEYS = (
    "home",
    "home_team",
    "homeTeam",
    "home_team_name",
    "homeTeamName",
    "home_name",
    "homeName",
    "team_home",
    "teamHome",
    "localteam",
    "local_team",
    "localTeam",
    "host",
    "team1",
    "competitor1",
    "participant1",
)

AWAY_KEYS = (
    "away",
    "away_team",
    "awayTeam",
    "away_team_name",
    "awayTeamName",
    "away_name",
    "awayName",
    "team_away",
    "teamAway",
    "visitorteam",
    "visitor_team",
    "visitorTeam",
    "guest",
    "team2",
    "competitor2",
    "participant2",
)

LEAGUE_KEYS = (
    "league",
    "competition",
    "tournament",
    "championship",
    "season",
    "category",
    "country",
)

DATE_KEYS = (
    "commence_time",
    "start_time",
    "startTime",
    "starts_at",
    "startsAt",
    "start_at",
    "startAt",
    "scheduled",
    "scheduled_at",
    "scheduledAt",
    "kickoff",
    "kickoff_time",
    "kickoffTime",
    "event_time",
    "eventTime",
    "match_time",
    "matchTime",
    "datetime",
    "date_time",
    "dateTime",
    "utcDate",
    "timestamp",
    "time",
    "date",
    "game_date",
    "gameDate",
)

ID_KEYS = (
    "id",
    "game_id",
    "gameId",
    "event_id",
    "eventId",
    "fixture_id",
    "fixtureId",
    "match_id",
    "matchId",
    "uuid",
    "slug",
)


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


def _first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_value(payload, keys)
    text = _text(value)
    return text if text else ""


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
            markers = [
                item.get("side"),
                item.get("home_away"),
                item.get("homeAway"),
                item.get("qualifier"),
                item.get("position"),
                item.get("type"),
                item.get("role"),
            ]
            marker_text = {str(marker or "").strip().lower() for marker in markers if marker not in (None, "")}
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
    nested_paths = (
        ("fixture", "home"),
        ("fixture", "homeTeam"),
        ("game", "home"),
        ("game", "homeTeam"),
        ("event", "home"),
        ("event", "homeTeam"),
        ("match", "home"),
        ("match", "homeTeam"),
    ) if side == "home" else (
        ("fixture", "away"),
        ("fixture", "awayTeam"),
        ("game", "away"),
        ("game", "awayTeam"),
        ("event", "away"),
        ("event", "awayTeam"),
        ("match", "away"),
        ("match", "awayTeam"),
    )
    for path in nested_paths:
        text = _text(_dig(row, *path))
        if text:
            return text
    return _team_from_side_lists(row, side)


def _league_name(row: dict[str, Any]) -> str:
    for key in LEAGUE_KEYS:
        text = _text(row.get(key))
        if text:
            return text
    for key in (
        "league_name",
        "leagueName",
        "competition_name",
        "competitionName",
        "tournament_name",
        "tournamentName",
        "sport_title",
        "sportTitle",
    ):
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
    try:
        from app.utils import parse_datetime
    except Exception:
        parse_datetime = None  # type: ignore[assignment]

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        try:
            if number > 1_000_000_000_000:
                return datetime.fromtimestamp(number / 1000.0, tz=UTC)
            if number > 1_000_000_000:
                return datetime.fromtimestamp(number, tz=UTC)
        except Exception:
            return None
    if isinstance(value, dict):
        for key in DATE_KEYS:
            nested = _parse_datetime_value(value.get(key))
            if nested is not None:
                return nested
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
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(normalized.replace(" ", "T", 1) if " " in normalized and "T" not in normalized else normalized)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except Exception:
        pass
    if parse_datetime is not None:
        try:
            dt = parse_datetime(raw)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def _fixture_datetime(row: dict[str, Any]) -> datetime | None:
    for key in DATE_KEYS:
        dt = _parse_datetime_value(row.get(key))
        if dt is not None:
            return dt.astimezone(UTC)
    for path in (
        ("fixture", "date"),
        ("fixture", "start_time"),
        ("game", "date"),
        ("game", "start_time"),
        ("event", "date"),
        ("match", "date"),
    ):
        dt = _parse_datetime_value(_dig(row, *path))
        if dt is not None:
            return dt.astimezone(UTC)
    date_part = row.get("date") or row.get("day") or row.get("game_date") or row.get("gameDate")
    time_part = row.get("time") or row.get("start") or row.get("hour")
    if date_part and time_part:
        dt = _parse_datetime_value(f"{date_part} {time_part}")
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
    if not isinstance(row, dict):
        return None
    home = _team_name(row, "home")
    away = _team_name(row, "away")
    if not home or not away:
        return None
    commence = _fixture_datetime(row)
    if commence is None:
        return None
    league = _league_name(row)
    event_id = _event_id(row)
    league_key = str(_dig(row, "league", "id") or _dig(row, "competition", "id") or row.get("league_id") or row.get("competition_id") or "")
    return Match(
        source="sportlogic",
        source_event_id=event_id,
        sport_key="soccer",
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=commence,
        home_team_norm="",
        away_team_norm="",
        league_key=league_key,
        tier="low" if _is_low_tier_text(league) else "mid",
        metadata={"sportlogic_fixture": row},
    )


def _parse_reject_reason(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return "not_dict"
    home = _team_name(row, "home")
    away = _team_name(row, "away")
    dt = _fixture_datetime(row)
    if not home and not away:
        return "missing_both_teams"
    if not home:
        return "missing_home_team"
    if not away:
        return "missing_away_team"
    if dt is None:
        return "missing_start_time"
    return "unknown"


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
        for offset in range(0, 3):
            day = now + timedelta(days=offset)
            key = day.date().isoformat()
            if key not in seen:
                seen.add(key)
                dates.append(day)
    return sorted(dates, key=lambda item: item.date())


def _target_window(matches: list[Any], now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    starts: list[datetime] = []
    for match in matches:
        try:
            starts.append(match.commence_time.astimezone(UTC))
        except Exception:
            pass
    if starts:
        return min(starts) - timedelta(hours=6), max(starts) + timedelta(hours=18)
    return now - timedelta(hours=3), now + timedelta(days=4)


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
        return max(1, min(8, int(float(os.getenv("SPORTLOGIC_FIXTURE_PAGE_SCAN_MAX", "5") or 5))))
    except Exception:
        return 5


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
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_fixture_parse": [], "errors": [], "fixture_query_attempts": []}
        dates = _collect_candidate_dates(matches)[:8]
        window_start, window_end = _target_window(matches)
        fixtures: list[dict[str, Any]] = []
        rows_by_date: dict[str, int] = {}
        empty_attempts = 0
        stale_rows = 0
        page_scan_max = _page_scan_max()

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for day in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_key = _date_key(day)
                before = len(fixtures)
                found_window_rows_for_day = False
                for base_params in _fixture_query_variants(day):
                    if found_window_rows_for_day:
                        break
                    for page in range(1, page_scan_max + 1):
                        if not self._budget_left():
                            stats["budget_exhausted"] = True
                            break
                        params = dict(base_params)
                        params["page"] = page
                        payload = await self._get_json(client, "/games", params, stats, preview)
                        rows = self._extract_list(payload)
                        kept = [row for row in rows if _row_in_window(row, window_start, window_end)]
                        stale_rows += max(0, len(rows) - len(kept))
                        attempt = {"date": day_key, "params": dict(params), "rows": len(rows), "kept": len(kept)}
                        if len(preview["fixture_query_attempts"]) < 28:
                            preview["fixture_query_attempts"].append(attempt)
                        stats.setdefault("fixture_query_attempts", []).append(attempt)
                        if rows and not preview["sample_fixture_parse"]:
                            preview["sample_fixture_parse"] = _sample_rows(rows, 3)
                            stats["sample_fixture_keys"] = [list(row.keys())[:40] for row in rows[:3] if isinstance(row, dict)]
                        if kept:
                            fixtures.extend(kept)
                            found_window_rows_for_day = True
                        if not rows:
                            empty_attempts += 1
                            break
                        if len(rows) < int(params.get("per_page") or 100):
                            break
                rows_by_date[day_key] = len(fixtures) - before

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
        if fixtures:
            preview["sample_fixture_parse_kept"] = _sample_rows(fixtures, 3)
        if not fixtures and not stats.get("response_errors"):
            stats["empty_games_reason"] = "all_fixture_rows_outside_target_window_or_empty_pages"
        return fixtures, stats, preview

    async def fetch_matches_patched(self: Any):
        stats = self._stats("matches")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_fixture_parse": [], "sample_matches": [], "errors": []}
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
        if fixture_preview.get("sample_fixture_parse_kept"):
            preview["sample_fixture_parse_kept"] = fixture_preview.get("sample_fixture_parse_kept", [])[:3]

        matches_out = []
        seen = set()
        horizon = now + timedelta(days=days_ahead + 1)
        parse_rejects: dict[str, int] = {}
        out_of_window = 0
        for row in fixtures:
            match = _row_to_match(row)
            if match is None:
                reason = _parse_reject_reason(row)
                parse_rejects[reason] = parse_rejects.get(reason, 0) + 1
                stats["fixtures_skipped"] += 1
                continue
            commence = match.commence_time.astimezone(UTC)
            if commence < now - timedelta(hours=3) or commence > horizon:
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
