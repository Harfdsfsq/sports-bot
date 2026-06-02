from __future__ import annotations

"""Expand the daily match inventory toward the configured 300-match target.

The normal inventory builder can only rank the fixtures it has already discovered.
When the primary odds feed returns fewer than 300 same-day soccer events, the day
inventory stays short even though the workflow env says TARGET=300.  This script
is a safe filler layer:

* it runs before the bot and again before the final report;
* it preserves existing odds/context evidence;
* it adds lower-priority skeleton fixtures from broad provider list endpoints;
* it never creates synthetic odds, value, xG, or publishable candidates;
* it keeps the inventory capped at DAY_INVENTORY_TARGET_SIZE / MAX_MATCHES.

Newly added rows are marked with metadata.inventory_target_fill=true and must be
enriched by normal runtime providers before they can become publishable.
"""

import asyncio
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.schemas import Match
from app.services.day_inventory import DayInventoryStore
from app.utils import canonicalize_league_name, canonicalize_team_name, is_low_tier_league, parse_datetime

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
CACHE_DIR = Path(".data/cache")
REPORT_PATH = EXPORT_DIR / "latest-day-inventory-target-fill.json"
CANDIDATE_CACHE_PATH = CACHE_DIR / "day-inventory-target-fill-candidates.json"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def app_tz(settings: Settings) -> ZoneInfo | timezone:
    name = str(getattr(settings, "app_timezone", None) or os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def target_local_date(settings: Settings) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz(settings)).date().isoformat()


def local_date_for(settings: Settings, dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(app_tz(settings)).date().isoformat()


def fill_window_dates(local_date: str) -> tuple[date, date]:
    """Return inclusive local-date window for inventory target fill.

    The canonical day inventory is still anchored to DAY_INVENTORY_TARGET_DATE, but
    the project rules allow the tracked pool to include >24h matches.  When the
    same-day feeds do not contain 300 real soccer fixtures, this optional rolling
    horizon fills the remaining inventory with future skeleton fixtures.  These rows
    are marked as target-fill rows and are not publishable until normal odds/context
    enrichment validates them.
    """
    start = date.fromisoformat(local_date)
    if not env_bool("DAY_INVENTORY_FILL_ALLOW_ROLLING_HORIZON", True):
        return start, start
    future_days = max(0, env_int("DAY_INVENTORY_FILL_FUTURE_DAYS", 3))
    return start, start + timedelta(days=future_days)


def in_fill_window(settings: Settings, dt: datetime | None, local_date: str) -> bool:
    if dt is None:
        return False
    day = dt.astimezone(app_tz(settings)).date()
    start, end = fill_window_dates(local_date)
    return start <= day <= end


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_key(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _dig(value: Any, path: str) -> Any:
    cur = value
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
        if cur in (None, ""):
            return None
    return cur


def first_value(row: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _dig(row, path)
        if value not in (None, ""):
            return value
    return None


def value_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "short_name", "display_name", "title", "team_name", "full_name", "slug"):
            if value.get(key) not in (None, ""):
                return clean_text(value.get(key))
        return ""
    if isinstance(value, str):
        return clean_text(value)
    return ""


def team_name(row: dict[str, Any], side: str) -> str:
    side_alt = "home" if side == "home" else "away"
    paths = [
        f"{side_alt}_team", f"{side_alt}Team", f"{side_alt}.name", f"{side_alt}Team.name",
        f"{side_alt}_team.name", f"{side_alt}_team_obj.name", f"{side_alt}_team_obj.short_name",
        f"{side_alt}_name", f"{side_alt}Name", f"{side_alt}TeamName",
        f"{side_alt}Club.name", f"{side_alt}_club.name",
        f"teams.{side_alt}.name", f"participants.{0 if side == 'home' else 1}.name",
        f"competitors.{0 if side == 'home' else 1}.name",
        "localteam.name" if side == "home" else "visitorteam.name",
        "localTeam.name" if side == "home" else "visitorTeam.name",
    ]
    direct = first_value(row, paths)
    name = value_name(direct)
    if name:
        return name
    # SportLogic and some feeds put home/away inside nested game/event objects.
    for wrapper in ("event", "game", "fixture", "match"):
        nested = row.get(wrapper)
        if isinstance(nested, dict):
            name = team_name(nested, side)
            if name:
                return name
    return ""


def league_name(row: dict[str, Any]) -> str:
    value = first_value(row, [
        "league_name", "league.name", "league.title", "competition", "competition.name",
        "tournament.name", "tournament", "country", "season.league.name", "sport.league.name",
    ])
    name = value_name(value)
    if name:
        return name
    for wrapper in ("event", "game", "fixture", "match"):
        nested = row.get(wrapper)
        if isinstance(nested, dict):
            name = league_name(nested)
            if name:
                return name
    return "Unknown"


def event_id(row: dict[str, Any]) -> str:
    value = first_value(row, [
        "id", "event_id", "eventId", "game_id", "gameId", "fixture_id", "fixtureId",
        "match_id", "matchId", "source_event_id", "flashId", "external_id", "uuid",
        "event.id", "game.id", "fixture.id", "match.id",
    ])
    if value not in (None, ""):
        return str(value).strip()
    return ""


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        # milliseconds or seconds epoch
        if number > 10_000_000_000:
            number /= 1000.0
        if number > 1_000_000_000:
            try:
                return datetime.fromtimestamp(number, UTC)
            except Exception:
                return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_dt(int(text))
    try:
        dt = parse_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def kickoff_dt(row: dict[str, Any]) -> datetime | None:
    value = first_value(row, [
        "commence_time", "commenceTime", "kickoff", "kickoff_utc", "start_time", "startTime",
        "event_date", "eventDate", "date", "dateUtc", "date_utc", "utcDate", "gameDate",
        "scheduled", "time", "timestamp", "startTimestamp", "start_at", "starts_at",
        "event.commence_time", "event.event_date", "event.date", "game.date", "fixture.date",
    ])
    dt = parse_dt(value)
    if dt is not None:
        return dt
    for wrapper in ("event", "game", "fixture", "match"):
        nested = row.get(wrapper)
        if isinstance(nested, dict):
            dt = kickoff_dt(nested)
            if dt is not None:
                return dt
    return None


def is_low_quality_text(league: str, home: str, away: str) -> bool:
    text = " ".join([league or "", home or "", away or ""]).lower()
    if is_low_tier_league(league):
        return True
    return bool(re.search(r"\b(u[- ]?(17|18|19|20|21|23)|under[- ]?(17|18|19|20|21|23)|reserves?|youth|academy|women(?:s)?|2nd|second team|b team)\b", text))


def make_match_from_row(row: dict[str, Any], *, source: str, settings: Settings, metadata_extra: dict[str, Any] | None = None) -> Match | None:
    home = team_name(row, "home")
    away = team_name(row, "away")
    start = kickoff_dt(row)
    if not home or not away or start is None:
        return None
    league = league_name(row)
    sid = event_id(row)
    low = is_low_quality_text(league, home, away)
    metadata: dict[str, Any] = {
        "inventory_target_fill": True,
        "inventory_fill_source": source,
        "provider_source_ids": {source: sid} if sid else {},
        "sources_seen": source,
        "core_inventory": True,
        "inventory_fill_window": True,
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    if source == "odds_api_io":
        metadata["has_current_odds_provider"] = True
    if source == "sstats":
        metadata["sstats_has_context_hint"] = True
    if source == "bzzoiro":
        metadata["bzzoiro_has_context_hint"] = True
    if source == "sportlogic":
        metadata["sportlogic_has_context_hint"] = True
    return Match(
        source=source,
        source_event_id=sid,
        sport_key="soccer",
        league_name=league or "Unknown",
        home_team=home,
        away_team=away,
        commence_time=start,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league or "Unknown"),
        tier="low" if low else "mid",
        metadata=metadata,
    )


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "events", "items", "matches", "games", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        if isinstance(payload.get("data"), dict):
            for key in ("results", "events", "items", "matches", "games"):
                value = payload["data"].get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
    return []


def existing_payload(settings: Settings, local_date: str) -> dict[str, Any]:
    candidates = [
        Path(f".data/day_inventory/{local_date}.json"),
        Path(".data/day_inventory/latest.json"),
        Path(".data/day_inventory/current.json"),
        Path(".data/day_inventory/today.json"),
        Path(".data/cache/day_inventory/latest.json"),
    ]
    best: dict[str, Any] = {}
    best_count = -1
    for path in candidates:
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        count = len(rows)
        if count > best_count:
            best = payload
            best_count = count
    if not best:
        return {"date_local": local_date, "matches": [], "counts": {"matches_total": 0}}
    return best


def serialize_for_cache(matches: list[Match]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in matches:
        row = asdict(match)
        row["commence_time"] = match.commence_time.astimezone(UTC).isoformat()
        out.append(row)
    return out


def matches_from_cache(rows: Any) -> list[Match]:
    out: list[Match] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            dt = parse_dt(row.get("commence_time"))
            if dt is None:
                continue
            payload = dict(row)
            payload["commence_time"] = dt
            out.append(Match(**payload))
        except Exception:
            continue
    return out


async def fetch_odds_api_io(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_FILL_USE_ODDS_API_IO", True), "matches": 0, "errors": 0}
    if not stats["enabled"]:
        return [], stats
    try:
        from app.services.runner import PredictionRunner
        old_provider = os.environ.get("MATCH_BOOTSTRAP_PROVIDER")
        old_allow_low = os.environ.get("DAY_INVENTORY_ALLOW_LOW_TIER")
        if env_bool("DAY_INVENTORY_FILL_INCLUDE_LOW_TIER_ODDS", True):
            os.environ["DAY_INVENTORY_ALLOW_LOW_TIER"] = "true"
        os.environ["MATCH_BOOTSTRAP_PROVIDER"] = "odds_api_io"
        try:
            runner = PredictionRunner(settings)
            matches, meta = await runner._fetch_matches()  # noqa: SLF001
            deduped = runner._dedupe_matches(matches)  # noqa: SLF001
        finally:
            if old_provider is None:
                os.environ.pop("MATCH_BOOTSTRAP_PROVIDER", None)
            else:
                os.environ["MATCH_BOOTSTRAP_PROVIDER"] = old_provider
            if old_allow_low is None:
                os.environ.pop("DAY_INVENTORY_ALLOW_LOW_TIER", None)
            else:
                os.environ["DAY_INVENTORY_ALLOW_LOW_TIER"] = old_allow_low
        rows: list[Match] = []
        for match in deduped:
            if getattr(match, "sport_key", "") != "soccer":
                continue
            if not in_fill_window(settings, match.commence_time, local_date):
                continue
            meta2 = dict(match.metadata or {})
            source_ids = dict(meta2.get("provider_source_ids") or {})
            if match.source_event_id:
                source_ids["odds_api_io"] = str(match.source_event_id)
            meta2.update({
                "provider_source_ids": source_ids,
                "sources_seen": ",".join(sorted({"odds_api_io", *[x for x in str(meta2.get("sources_seen") or "").split(",") if x]})),
                "inventory_target_fill": bool(meta2.get("inventory_target_fill")) or True,
                "inventory_fill_source": "odds_api_io",
                "has_current_odds_provider": True,
                "core_inventory": True,
            })
            rows.append(Match(**{**asdict(match), "source": "odds_api_io", "metadata": meta2}))
        stats["matches"] = len(rows)
        stats["runner_meta"] = meta if isinstance(meta, dict) else {}
        return rows, stats
    except Exception as exc:
        stats["errors"] = 1
        stats["last_error"] = f"{type(exc).__name__}: {exc}"
        return [], stats


async def fetch_bzzoiro(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_FILL_USE_BZZOIRO", True), "matches": 0, "rows": 0, "requests": 0, "errors": 0, "http_statuses": []}
    key = str(os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", "") or "").strip()
    if not stats["enabled"] or not key:
        stats["enabled"] = bool(stats["enabled"])
        stats["api_key_present"] = bool(key)
        return [], stats
    target = date.fromisoformat(local_date)
    start_day, end_day = fill_window_dates(local_date)
    window = env_int("DAY_INVENTORY_FILL_BZZOIRO_WINDOW_DAYS", 0)
    date_from = (start_day - timedelta(days=max(0, window))).isoformat()
    date_to = (end_day + timedelta(days=max(0, window))).isoformat()
    base = str(os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
    headers = {"Authorization": f"Token {key}"}
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=env_float("BZZOIRO_TIMEOUT_SECONDS", 20.0), follow_redirects=True) as client:
        max_pages = max(1, env_int("DAY_INVENTORY_FILL_BZZOIRO_MAX_PAGES", 6))
        limit = max(50, env_int("DAY_INVENTORY_FILL_BZZOIRO_PAGE_SIZE", 200))
        for page in range(max_pages):
            params = {"date_from": date_from, "date_to": date_to, "limit": limit, "offset": page * limit}
            try:
                stats["requests"] += 1
                resp = await client.get(f"{base}/events/", headers=headers, params=params)
                stats["http_statuses"].append(resp.status_code)
                if resp.status_code != 200:
                    stats["errors"] += 1
                    stats["last_body_preview"] = resp.text[:800]
                    break
                batch = rows_from_payload(resp.json())
                rows.extend(batch)
                if len(batch) < limit:
                    break
            except Exception as exc:
                stats["errors"] += 1
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
                break
        # v1 predictions can expose events missing from v2 events on some accounts.
        max_pred_pages = max(0, env_int("DAY_INVENTORY_FILL_BZZOIRO_PREDICTION_PAGES", 4))
        for page in range(1, max_pred_pages + 1):
            try:
                stats["requests"] += 1
                resp = await client.get(
                    "https://sports.bzzoiro.com/api/predictions/",
                    headers=headers,
                    params={"date_from": date_from, "date_to": date_to, "upcoming": "true", "tz": "UTC", "page": page},
                )
                stats["http_statuses"].append(resp.status_code)
                if resp.status_code != 200:
                    stats["errors"] += 1
                    break
                payload = resp.json()
                batch = rows_from_payload(payload)
                rows.extend(batch)
                if not isinstance(payload, dict) or not payload.get("next") or not batch:
                    break
            except Exception as exc:
                stats["errors"] += 1
                stats["prediction_error"] = f"{type(exc).__name__}: {exc}"
                break
    stats["rows"] = len(rows)
    matches: list[Match] = []
    for row in rows:
        source_row = row.get("event") if isinstance(row.get("event"), dict) else row
        match = make_match_from_row(source_row, source="bzzoiro", settings=settings, metadata_extra={"bzzoiro_raw_source": "prediction_event" if row.get("event") else "events_v2"})
        if match is not None and in_fill_window(settings, match.commence_time, local_date):
            matches.append(match)
    stats["matches"] = len({m.match_key for m in matches})
    return matches, stats


async def fetch_sstats(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_FILL_USE_SSTATS_AS_FIXTURE_SOURCE", True), "matches": 0, "rows": 0, "requests": 0, "errors": 0, "http_statuses": []}
    key = str(os.getenv("SSTATS_API_KEY") or getattr(settings, "sstats_api_key", "") or "").strip()
    if not stats["enabled"] or not key:
        stats["api_key_present"] = bool(key)
        return [], stats
    target = date.fromisoformat(local_date)
    start_day, end_day = fill_window_dates(local_date)
    window = env_int("DAY_INVENTORY_FILL_SSTATS_WINDOW_DAYS", 0)
    date_from = (start_day - timedelta(days=max(0, window))).isoformat()
    date_to = (end_day + timedelta(days=max(0, window))).isoformat()
    limit = max(100, env_int("DAY_INVENTORY_FILL_SSTATS_PAGE_SIZE", 1000))
    max_requests = max(1, env_int("DAY_INVENTORY_FILL_SSTATS_MAX_REQUESTS", 18))
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=env_float("SSTATS_TIMEOUT_SECONDS", 25.0), follow_redirects=True) as client:
        offset = 0
        while stats["requests"] < max_requests:
            params = {"from": date_from, "to": date_to, "limit": limit, "offset": offset, "apikey": key}
            try:
                stats["requests"] += 1
                resp = await client.get("https://api.sstats.net/Games/list", params=params)
                stats["http_statuses"].append(resp.status_code)
                if resp.status_code != 200:
                    stats["errors"] += 1
                    stats["last_body_preview"] = resp.text[:800]
                    break
                batch = rows_from_payload(resp.json())
                rows.extend(batch)
                if len(batch) < limit:
                    break
                offset += len(batch)
            except Exception as exc:
                stats["errors"] += 1
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
                break
    stats["rows"] = len(rows)
    matches: list[Match] = []
    for row in rows:
        match = make_match_from_row(row, source="sstats", settings=settings, metadata_extra={"sstats_fixture_fill": True})
        if match is not None and in_fill_window(settings, match.commence_time, local_date):
            matches.append(match)
    stats["matches"] = len({m.match_key for m in matches})
    return matches, stats


def sportlogic_game_ids_from_odds_row(row: dict[str, Any]) -> list[str]:
    keys = ["game_id", "gameId", "event_id", "eventId", "fixture_id", "fixtureId", "match_id", "matchId", "id_game", "game.id", "event.id"]
    out: list[str] = []
    for key in keys:
        value = _dig(row, key)
        if value not in (None, ""):
            text = str(value).strip()
            if text and text not in out:
                out.append(text)
    # Scan one nesting level for common linked game objects.
    for value in row.values():
        if isinstance(value, dict):
            nested_id = event_id(value)
            if nested_id and nested_id not in out:
                out.append(nested_id)
    return out[:4]


async def fetch_sportlogic(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_FILL_USE_SPORTLOGIC", True), "matches": 0, "rows": 0, "requests": 0, "errors": 0, "http_statuses": [], "active_odds_rows": 0, "active_game_ids": 0}
    key = str(os.getenv("SPORTLOGIC_API_KEY") or os.getenv("SPORTLOGIC_KEY") or os.getenv("SPORTLOGIC_TOKEN") or getattr(settings, "sportlogic_api_key", "") or "").strip()
    if not stats["enabled"] or not key:
        stats["api_key_present"] = bool(key)
        return [], stats
    base = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    header_name = str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key").strip() or "X-API-Key"
    headers = {"Accept": "application/json", header_name: key}
    target = date.fromisoformat(local_date)
    date_from = (target - timedelta(days=env_int("DAY_INVENTORY_FILL_SPORTLOGIC_WINDOW_DAYS", 0))).isoformat()
    date_to = (target + timedelta(days=env_int("DAY_INVENTORY_FILL_SPORTLOGIC_WINDOW_DAYS", 1))).isoformat()
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=env_float("SPORTLOGIC_TIMEOUT_SECONDS", 20.0), follow_redirects=True) as client:
        for params in (
            {"date_from": date_from, "date_to": date_to, "per_page": 100, "status": "scheduled"},
            {"date_from": date_from, "date_to": date_to, "per_page": 100},
        ):
            try:
                stats["requests"] += 1
                resp = await client.get(f"{base}/games", headers=headers, params=params)
                stats["http_statuses"].append(resp.status_code)
                if resp.status_code == 200:
                    batch = rows_from_payload(resp.json())
                    rows.extend(batch)
                    if batch:
                        break
                else:
                    stats["errors"] += 1
            except Exception as exc:
                stats["errors"] += 1
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
        # Active odds recovery: fetch game ids from /odds and link back to /games/{id}.
        if env_bool("DAY_INVENTORY_FILL_SPORTLOGIC_ACTIVE_ODDS_RECOVERY", True):
            game_ids: list[str] = []
            max_pages = max(1, env_int("DAY_INVENTORY_FILL_SPORTLOGIC_ACTIVE_ODDS_PAGES", 2))
            max_games = max(1, env_int("DAY_INVENTORY_FILL_SPORTLOGIC_ACTIVE_GAME_LIMIT", 60))
            cursor = ""
            for _ in range(max_pages):
                try:
                    params: dict[str, Any] = {"is_active": "true", "per_page": 100}
                    if cursor:
                        params["cursor"] = cursor
                    stats["requests"] += 1
                    resp = await client.get(f"{base}/odds", headers=headers, params=params)
                    stats["http_statuses"].append(resp.status_code)
                    if resp.status_code != 200:
                        stats["errors"] += 1
                        break
                    payload = resp.json()
                    odds_rows = rows_from_payload(payload)
                    stats["active_odds_rows"] += len(odds_rows)
                    for odds_row in odds_rows:
                        for gid in sportlogic_game_ids_from_odds_row(odds_row):
                            if gid not in game_ids:
                                game_ids.append(gid)
                                break
                        if len(game_ids) >= max_games:
                            break
                    cursor = ""
                    if isinstance(payload, dict):
                        cursor = str(payload.get("next_cursor") or payload.get("next") or _dig(payload, "pagination.next_cursor") or "")
                    if len(game_ids) >= max_games or not cursor:
                        break
                except Exception as exc:
                    stats["errors"] += 1
                    stats["active_odds_error"] = f"{type(exc).__name__}: {exc}"
                    break
            stats["active_game_ids"] = len(game_ids)
            for gid in game_ids[:max_games]:
                try:
                    stats["requests"] += 1
                    resp = await client.get(f"{base}/games/{gid}", headers=headers)
                    stats["http_statuses"].append(resp.status_code)
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    batch = rows_from_payload(payload)
                    if not batch and isinstance(payload, dict):
                        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                        if isinstance(data, dict):
                            batch = [data]
                    rows.extend(batch)
                except Exception:
                    continue
    stats["rows"] = len(rows)
    matches: list[Match] = []
    for row in rows:
        match = make_match_from_row(row, source="sportlogic", settings=settings, metadata_extra={"sportlogic_fixture_fill": True})
        if match is not None and in_fill_window(settings, match.commence_time, local_date):
            matches.append(match)
    stats["matches"] = len({m.match_key for m in matches})
    return matches, stats


def source_values(row: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    raw = row.get("sources_seen")
    if isinstance(raw, list):
        sources.update(str(x).strip() for x in raw if str(x).strip())
    elif isinstance(raw, str):
        sources.update(x.strip() for x in raw.split(",") if x.strip())
    if isinstance(row.get("source_ids"), dict):
        sources.update(str(k).strip() for k in row["source_ids"].keys() if str(k).strip())
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if isinstance(meta.get("provider_source_ids"), dict):
        sources.update(str(k).strip() for k in meta["provider_source_ids"].keys() if str(k).strip())
    return {s for s in sources if s}


def parse_row_dt(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff") or row.get("start_time"))


def row_priority(row: dict[str, Any]) -> float:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    sources = source_values(row)
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    now = datetime.now(UTC)
    kickoff = parse_row_dt(row)
    if kickoff is None:
        hours = 999.0
    else:
        hours = (kickoff - now).total_seconds() / 3600.0
    score = 0.0
    if 0 <= hours <= 4:
        score += 180
    elif 4 < hours <= 8:
        score += 160
    elif 8 < hours <= 12:
        score += 140
    elif 12 < hours <= 24:
        score += 115
    elif hours > 24:
        score += 80
    else:
        score += 25
    if coverage.get("odds"):
        score += 45
    if coverage.get("context"):
        score += 30
    if coverage.get("ready_for_model"):
        score += 35
    score += min(45, len(sources) * 12)
    if "odds_api_io" in sources:
        score += 28
    if "bzzoiro" in sources:
        score += 15
    if "sstats" in sources:
        score += 12
    if "sportlogic" in sources:
        score += 10
    league = str(row.get("league_name") or "").lower()
    if any(term in league for term in ("premier", "serie a", "la liga", "bundesliga", "ligue 1", "eredivisie", "mls", "championship", "botola", "serie b")):
        score += 10
    if str(row.get("tier") or "").lower() == "low" or bool(meta.get("inventory_fill_low_priority")):
        score -= 35
    return round(score, 3)


def enrich_and_recount(payload: dict[str, Any], target_size: int) -> dict[str, Any]:
    rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("canonical_match_id") or row.get("match_key") or "").strip()
        if not key:
            home = norm_key(row.get("home_team"))
            away = norm_key(row.get("away_team"))
            day = str(row.get("date_local") or "")
            key = f"soccer|{home}|{away}|{day}"
            row["canonical_match_id"] = key
            row["match_key"] = key
        current = unique.get(key)
        if current is None or row_priority(row) > row_priority(current):
            unique[key] = row
        else:
            # Preserve source ids from duplicates.
            ids = dict(current.get("source_ids") or {})
            ids.update(row.get("source_ids") or {})
            current["source_ids"] = ids
            current["sources_seen"] = sorted({*source_values(current), *source_values(row), *ids.keys()})
    sorted_rows = sorted(unique.values(), key=lambda item: (-row_priority(item), str(item.get("kickoff_utc") or ""), str(item.get("league_name") or ""), str(item.get("home_team") or "")))[:target_size]

    counts: dict[str, int] = {
        "matches_total": len(sorted_rows),
        "matches_with_odds": 0,
        "matches_with_context": 0,
        "matches_with_weather": 0,
        "matches_with_news": 0,
        "matches_with_xg": 0,
        "matches_with_form": 0,
        "matches_ready_for_model": 0,
        "matches_ready_for_publish": 0,
        "matches_next_6h": 0,
        "matches_next_6h_ready": 0,
        "matches_next_12h": 0,
        "matches_next_12h_ready": 0,
        "matches_with_2plus_core_fixture_sources": 0,
    }
    all_source_counts: dict[str, int] = {}
    league_counts: dict[str, int] = {}
    now = datetime.now(UTC)
    for row in sorted_rows:
        sources = source_values(row)
        row["sources_seen"] = sorted(sources)
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        coverage = dict(row.get("coverage") or {})
        has_odds = bool(coverage.get("odds")) or "odds_api_io" in sources or bool(meta.get("has_current_odds_provider"))
        has_context = bool(coverage.get("context")) or bool({"bzzoiro", "sstats", "sportlogic"} & sources) or bool(meta.get("bzzoiro_has_context_hint") or meta.get("sstats_has_context_hint") or meta.get("sportlogic_has_context_hint"))
        has_xg = bool(coverage.get("xg")) or bool(meta.get("bzzoiro_context_fields") or meta.get("sstats_context_fields"))
        has_form = bool(coverage.get("form")) or "sstats" in sources
        coverage.update({
            "fixture_core": True,
            "odds": bool(has_odds),
            "context": bool(has_context),
            "xg": bool(has_xg),
            "form": bool(has_form),
            "ready_for_model": bool(has_odds and has_context),
            "ready_for_publish": bool(coverage.get("ready_for_publish")) and bool(has_odds and has_context),
        })
        row["coverage"] = coverage
        row["priority"] = max(float(row.get("priority") or 0.0), row_priority(row))
        league = str(row.get("league_name") or "")
        if league:
            league_counts[league] = league_counts.get(league, 0) + 1
        for src in sources:
            all_source_counts[src] = all_source_counts.get(src, 0) + 1
        counts["matches_with_odds"] += int(coverage["odds"])
        counts["matches_with_context"] += int(coverage["context"])
        counts["matches_with_xg"] += int(coverage["xg"])
        counts["matches_with_form"] += int(coverage["form"])
        counts["matches_ready_for_model"] += int(coverage["ready_for_model"])
        counts["matches_ready_for_publish"] += int(coverage["ready_for_publish"])
        counts["matches_with_2plus_core_fixture_sources"] += int(len(sources & {"odds_api_io", "bzzoiro", "sstats", "sportlogic"}) >= 2)
        kickoff = parse_row_dt(row)
        if kickoff is not None:
            hours = (kickoff - now).total_seconds() / 3600.0
            if 0 <= hours <= 6:
                counts["matches_next_6h"] += 1
                counts["matches_next_6h_ready"] += int(coverage["ready_for_model"])
            if 0 <= hours <= 12:
                counts["matches_next_12h"] += 1
                counts["matches_next_12h_ready"] += int(coverage["ready_for_model"])
    payload["matches"] = sorted_rows
    payload.setdefault("counts", {}).update(counts)
    payload["counts"]["target_matches"] = target_size
    payload["counts"]["target_shortfall"] = max(0, target_size - len(sorted_rows))
    payload["counts"]["target_full"] = len(sorted_rows) >= target_size
    payload["all_source_match_counts"] = dict(sorted(all_source_counts.items(), key=lambda item: (-item[1], item[0])))
    payload["league_match_counts"] = dict(sorted(league_counts.items(), key=lambda item: (-item[1], item[0]))[:80])
    payload["updated_at_utc"] = datetime.now(UTC).isoformat()
    payload["build_status"] = payload.get("build_status") or "ok"
    return payload


async def discover_candidates(settings: Settings, local_date: str, target_size: int) -> tuple[list[Match], dict[str, Any]]:
    cache_ttl_minutes = env_int("DAY_INVENTORY_FILL_CANDIDATE_CACHE_TTL_MINUTES", 90)
    ignore_short_cache = env_bool("DAY_INVENTORY_FILL_IGNORE_SHORT_CACHE", True)
    cached = load_json(CANDIDATE_CACHE_PATH, {})
    if cache_ttl_minutes > 0 and isinstance(cached, dict) and cached.get("local_date") == local_date:
        ts = parse_dt(cached.get("created_at_utc"))
        if ts is not None and datetime.now(UTC) - ts <= timedelta(minutes=cache_ttl_minutes):
            matches = matches_from_cache(cached.get("matches"))
            cached_count = len({m.match_key for m in matches})
            min_acceptable = max(1, int(target_size * env_float("DAY_INVENTORY_FILL_CACHE_MIN_TARGET_RATIO", 0.92)))
            if matches and (not ignore_short_cache or cached_count >= min_acceptable):
                return matches, {"cache_used": True, "cached_matches": cached_count, "created_at_utc": cached.get("created_at_utc"), "cache_min_acceptable": min_acceptable}
            if matches:
                # Keep this diagnostic but force fresh discovery: a short cache is exactly
                # why the inventory stayed at 148 instead of moving toward 300.
                cached_short_diag = {"cache_used": False, "cache_ignored_reason": "short_cache_below_target", "cached_matches": cached_count, "cache_min_acceptable": min_acceptable, "created_at_utc": cached.get("created_at_utc")}
            else:
                cached_short_diag = {"cache_used": False, "cache_ignored_reason": "empty_cache"}
        else:
            cached_short_diag = {"cache_used": False, "cache_ignored_reason": "expired_cache"}
    else:
        cached_short_diag = {"cache_used": False, "cache_ignored_reason": "cache_disabled_or_date_mismatch"}

    results = await asyncio.gather(
        fetch_odds_api_io(settings, local_date),
        fetch_bzzoiro(settings, local_date),
        fetch_sstats(settings, local_date),
        fetch_sportlogic(settings, local_date),
        return_exceptions=True,
    )
    provider_names = ["odds_api_io", "bzzoiro", "sstats", "sportlogic"]
    matches: list[Match] = []
    stats: dict[str, Any] = {**cached_short_diag, "providers": {}}
    for name, result in zip(provider_names, results):
        if isinstance(result, Exception):
            stats["providers"][name] = {"enabled": True, "errors": 1, "last_error": f"{type(result).__name__}: {result}"}
            continue
        provider_matches, provider_stats = result
        matches.extend(provider_matches)
        stats["providers"][name] = provider_stats
    write_json(CANDIDATE_CACHE_PATH, {"local_date": local_date, "created_at_utc": datetime.now(UTC).isoformat(), "matches": serialize_for_cache(matches), "stats": stats})
    return matches, stats


async def main_async() -> int:
    if not env_bool("DAY_INVENTORY_FILL_TO_TARGET_ENABLED", True):
        write_json(REPORT_PATH, {"enabled": False, "reason": "DAY_INVENTORY_FILL_TO_TARGET_ENABLED=false"})
        return 0

    settings = Settings()
    local_date = target_local_date(settings)
    target_size = env_int("DAY_INVENTORY_FILL_TARGET_SIZE", env_int("DAY_INVENTORY_TARGET_SIZE", env_int("DAY_INVENTORY_MAX_MATCHES", 300)))
    target_size = max(1, min(target_size, env_int("DAY_INVENTORY_FILL_HARD_MAX_MATCHES", 300)))
    store = DayInventoryStore(timezone_name=str(getattr(settings, "app_timezone", "Europe/Moscow") or "Europe/Moscow"))

    before_payload = existing_payload(settings, local_date)
    before_rows = before_payload.get("matches") if isinstance(before_payload.get("matches"), list) else []
    before_count = len(before_rows)

    report: dict[str, Any] = {
        "enabled": True,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "local_date": local_date,
        "target_size": target_size,
        "before_count": before_count,
    }

    if before_count >= target_size:
        payload = enrich_and_recount(before_payload, target_size)
        paths = store.save_inventory(payload)
        report.update({"status": "already_full", "after_count": len(payload.get("matches") or []), "target_shortfall": 0, "saved_paths": paths})
        write_json(REPORT_PATH, report)
        return 0

    candidates, discovery_stats = await discover_candidates(settings, local_date, target_size)
    existing = before_payload if isinstance(before_payload, dict) else {}
    source_meta = dict(existing.get("sources") or {}) if isinstance(existing.get("sources"), dict) else {}
    source_meta["inventory_target_fill"] = {
        "enabled": True,
        "created_at_utc": report["created_at_utc"],
        "target_size": target_size,
        "before_count": before_count,
        "discovery_stats": discovery_stats,
    }
    payload = store.build_payload(local_date=local_date, matches=candidates, source_meta=source_meta, existing=existing)
    payload = enrich_and_recount(payload, target_size)
    paths = store.save_inventory(payload)

    after_rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    added_keys = {
        str(row.get("canonical_match_id") or row.get("match_key"))
        for row in after_rows
        if isinstance(row, dict)
    } - {
        str(row.get("canonical_match_id") or row.get("match_key"))
        for row in before_rows
        if isinstance(row, dict)
    }
    added_by_source: dict[str, int] = {}
    for row in after_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("canonical_match_id") or row.get("match_key"))
        if key not in added_keys:
            continue
        sources = source_values(row) or {str((row.get("metadata") or {}).get("inventory_fill_source") or "unknown")}
        for src in sources:
            added_by_source[src] = added_by_source.get(src, 0) + 1

    report.update({
        "status": "expanded" if len(after_rows) > before_count else "no_expansion",
        "after_count": len(after_rows),
        "added_count": max(0, len(after_rows) - before_count),
        "added_new_keys_count": len(added_keys),
        "added_by_source": dict(sorted(added_by_source.items(), key=lambda item: (-item[1], item[0]))),
        "target_shortfall": max(0, target_size - len(after_rows)),
        "target_full": len(after_rows) >= target_size,
        "discovery_stats": discovery_stats,
        "counts": payload.get("counts"),
        "all_source_match_counts": payload.get("all_source_match_counts"),
        "saved_paths": paths,
    })
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
