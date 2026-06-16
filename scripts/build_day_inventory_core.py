from __future__ import annotations

"""Build canonical daily inventory from core fixture providers.

Core fixture providers:
- odds_api_io: primary fixture + current odds event ids through existing runner bootstrap;
- bzzoiro: v2 event discovery with source ids and lightweight context hints;
- sstats: Games/list fixture discovery plus lightweight context ids;
- sportlogic: quota-governed fixture discovery and later independent odds source.
"""

import asyncio
import json
import os
import re
import sys
from collections import Counter
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
from app.services.runner import PredictionRunner
from app.utils import (
    canonicalize_league_name,
    canonicalize_team_name,
    is_low_tier_league,
    parse_datetime,
    score_event_match_variants,
)

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
SUMMARY_PATH = EXPORT_DIR / "latest-day-inventory-summary.json"
CORE_REPORT_PATH = EXPORT_DIR / "latest-day-inventory-core-build-report.json"
CROSSWALK_PATH = EXPORT_DIR / "latest-day-inventory-core-crosswalk.json"

CORE_PROVIDERS = ("odds_api_io", "bzzoiro", "sstats", "sportlogic")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw))) if raw not in (None, "") else default
    except Exception:
        return default


def app_tz(settings: Settings) -> ZoneInfo | timezone:
    name = str(getattr(settings, "app_timezone", "") or os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def target_local_date(settings: Settings) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz(settings)).date().isoformat()


def to_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = parse_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def local_date_for(settings: Settings, dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(app_tz(settings)).date().isoformat()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def event_id(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    for key in ("id", "Id", "event_id", "gameId", "GameId", "GameID", "game_id", "flashId", "source_event_id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("results", "data", "events", "items", "matches", "games"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def nested(row: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = row
        ok = True
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur.get(part)
        if ok and cur not in (None, ""):
            return cur
    return None


LOW_QUALITY_TEAM_PATTERNS = [
    r"\bu[- ]?17\b", r"\bu[- ]?18\b", r"\bu[- ]?19\b", r"\bu[- ]?20\b", r"\bu[- ]?21\b", r"\bu[- ]?23\b",
    r"\bunder[- ]?17\b", r"\bunder[- ]?18\b", r"\bunder[- ]?19\b", r"\bunder[- ]?20\b", r"\bunder[- ]?21\b", r"\bunder[- ]?23\b",
    r"\breserves?\b", r"\breserve team\b", r"\byouth\b", r"\bacademy\b", r"\bwomen(?:s)?\b",
    r"\bii\b", r"\biii\b", r"\b2nd\b", r"\bsecond team\b", r"\bb team\b",
    r"\bvtoraya\s+liga\b", r"\bsecond\s+league\b", r"\bthird\s+league\b",
]


def is_low_quality_team_name(name: str) -> bool:
    text = str(name or "").strip().lower()
    if not text:
        return False
    if re.search(r"\b(?:u[- ]?17|u[- ]?18|u[- ]?19|u[- ]?20|u[- ]?21|u[- ]?23|under[- ]?17|under[- ]?18|under[- ]?19|under[- ]?20|under[- ]?21|under[- ]?23|reserves?|youth|academy|development|women(?:s)?|2nd|second team|b team)\b", text):
        return True
    # Many providers encode reserve squads as "Team 2", "Team-2", "Team II" or "Team III".
    # Do this check only on team names, never on league names, so good leagues like LaLiga 2 are not penalized.
    if re.search(r"(?:^|[\s\-_.])(?:2|ii|iii)$", text):
        return True
    return False



def is_low_quality_inventory_match(league: str, home: str, away: str) -> bool:
    if is_low_tier_league(league):
        return True
    haystack = " ".join([str(league or ""), str(home or ""), str(away or "")]).lower()
    if "unknown" in haystack:
        return True
    if is_low_quality_team_name(home) or is_low_quality_team_name(away):
        return True
    return any(re.search(pattern, haystack) for pattern in LOW_QUALITY_TEAM_PATTERNS)


def make_match(
    *,
    source: str,
    source_event_id: str,
    league_name: str,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    metadata: dict[str, Any] | None = None,
) -> Match | None:
    home = clean_text(home_team)
    away = clean_text(away_team)
    league = clean_text(league_name) or "Unknown"
    if not home or not away or not commence_time:
        return None
    if not env_bool("DAY_INVENTORY_ALLOW_LOW_TIER", False) and is_low_quality_inventory_match(league, home, away):
        return None
    meta = dict(metadata or {})
    source_ids = dict(meta.get("provider_source_ids") or {})
    if source_event_id:
        source_ids[source] = str(source_event_id)
    meta["provider_source_ids"] = source_ids
    meta.setdefault("sources_seen", source)
    return Match(
        source=source,
        source_event_id=str(source_event_id or ""),
        sport_key="soccer",
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=commence_time,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league),
        tier="low" if is_low_tier_league(league) else "mid",
        metadata=meta,
    )


async def fetch_odds_api_io(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": True, "requests": 0, "response_errors": 0, "events_fetched": 0, "matches_built": 0, "matches_for_target_date": 0, "source": "runner_bootstrap"}
    try:
        os.environ["MATCH_BOOTSTRAP_PROVIDER"] = "odds_api_io"
        if hasattr(settings, "match_bootstrap_provider"):
            setattr(settings, "match_bootstrap_provider", "odds_api_io")
        runner = PredictionRunner(settings)
        matches, meta = await runner._fetch_matches()  # noqa: SLF001
        deduped = runner._dedupe_matches(matches)  # noqa: SLF001
        provider_stats = dict((meta or {}).get("stats") or {})
        stats.update({k: v for k, v in provider_stats.items() if k not in {"last_body_preview"}})
        rows = []
        for match in deduped:
            if getattr(match, "sport_key", "") != "soccer":
                continue
            if local_date_for(settings, match.commence_time) != local_date:
                continue
            meta2 = dict(match.metadata or {})
            source_ids = dict(meta2.get("provider_source_ids") or {})
            if match.source_event_id:
                source_ids["odds_api_io"] = str(match.source_event_id)
            meta2["provider_source_ids"] = source_ids
            meta2["sources_seen"] = ",".join(sorted({"odds_api_io", *(str(meta2.get("sources_seen") or "").split(","))})).strip(",")
            meta2["core_inventory"] = True
            meta2["has_current_odds_provider"] = True
            rows.append(Match(**{**asdict(match), "source": "odds_api_io", "metadata": meta2}))
        stats["matches_built"] = len(deduped)
        stats["matches_for_target_date"] = len(rows)
        return rows, stats
    except Exception as exc:
        stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
        stats["last_error"] = f"{type(exc).__name__}: {exc}"
        return [], stats


def bzzoiro_row_to_match(row: dict[str, Any], settings: Settings) -> Match | None:
    event = row.get("event") if isinstance(row.get("event"), dict) else row
    home_obj = event.get("home_team_obj") if isinstance(event, dict) and isinstance(event.get("home_team_obj"), dict) else {}
    away_obj = event.get("away_team_obj") if isinstance(event, dict) and isinstance(event.get("away_team_obj"), dict) else {}
    home = nested(event, "home_team", "home", "home_name", "homeTeam.name") or home_obj.get("name") or home_obj.get("short_name")
    away = nested(event, "away_team", "away", "away_name", "awayTeam.name") or away_obj.get("name") or away_obj.get("short_name")
    league_obj = event.get("league") if isinstance(event, dict) and isinstance(event.get("league"), dict) else {}
    league = nested(event, "league_name", "competition", "league.name") or league_obj.get("name") or "Unknown"
    start = to_utc(nested(event, "event_date", "start_time", "commence_time", "date", "kickoff"))
    if start is None:
        return None
    sid = event_id(event, "id", "event_id") or event_id(row, "id", "event_id")
    metadata = {
        "bzzoiro_raw_source": "predictions" if row.get("event") else "events_v2",
        "provider_source_ids": {"bzzoiro": sid} if sid else {},
        "sources_seen": "bzzoiro",
        "core_inventory": True,
        "bzzoiro_has_prediction": bool(row.get("event")),
        "bzzoiro_has_context_hint": True,
    }
    if any(key in row for key in ("prob_over_25", "expected_home_goals", "expected_away_goals", "prob_btts_yes")):
        metadata["bzzoiro_context_fields"] = True
    return make_match(source="bzzoiro", source_event_id=sid, league_name=str(league or "Unknown"), home_team=str(home or ""), away_team=str(away or ""), commence_time=start, metadata=metadata)


async def fetch_bzzoiro(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_ENABLE_BZZOIRO", True), "api_key_present": bool(os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", "")), "requests": 0, "response_errors": 0, "events_fetched": 0, "matches_built": 0, "matches_for_target_date": 0, "endpoints": {}}
    if not stats["enabled"] or not stats["api_key_present"]:
        return [], stats
    token = str(os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", "") or "").strip()
    headers = {"Authorization": f"Token {token}"}
    target = date.fromisoformat(local_date)
    date_from = (target - timedelta(days=env_int("DAY_INVENTORY_BZZOIRO_WINDOW_DAYS", 1))).isoformat()
    date_to = (target + timedelta(days=env_int("DAY_INVENTORY_BZZOIRO_WINDOW_DAYS", 1))).isoformat()
    max_requests = env_int("DAY_INVENTORY_BZZOIRO_MAX_REQUESTS", 8)
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=float(os.getenv("BZZOIRO_TIMEOUT_SECONDS") or 20.0), headers=headers) as client:
        # v2 events: cheap fixture discovery.
        if stats["requests"] < max_requests:
            try:
                stats["requests"] += 1
                resp = await client.get("https://sports.bzzoiro.com/api/v2/events/", params={"date_from": date_from, "date_to": date_to, "limit": 300, "offset": 0})
                stats["endpoints"]["events_v2_status"] = resp.status_code
                if resp.status_code == 200:
                    batch = iter_dicts(resp.json())
                    rows.extend(batch)
                    stats["endpoints"]["events_v2_rows"] = len(batch)
                else:
                    stats["response_errors"] += 1
            except Exception as exc:
                stats["response_errors"] += 1; stats["events_v2_error"] = f"{type(exc).__name__}: {exc}"
        # v1 predictions: adds context-rich rows and often current odds hints.
        page = 1
        max_pages = env_int("DAY_INVENTORY_BZZOIRO_MAX_PAGES", 4)
        while stats["requests"] < max_requests and page <= max_pages:
            try:
                stats["requests"] += 1
                resp = await client.get("https://sports.bzzoiro.com/api/predictions/", params={"date_from": date_from, "date_to": date_to, "upcoming": "true", "tz": "UTC", "page": page})
                stats["endpoints"][f"predictions_page_{page}_status"] = resp.status_code
                if resp.status_code != 200:
                    stats["response_errors"] += 1
                    break
                payload = resp.json()
                batch = iter_dicts(payload)
                if not batch:
                    break
                rows.extend(batch)
                if not isinstance(payload, dict) or not payload.get("next"):
                    break
                page += 1
            except Exception as exc:
                stats["response_errors"] += 1; stats["predictions_error"] = f"{type(exc).__name__}: {exc}"; break
    stats["events_fetched"] = len(rows)
    matches: list[Match] = []
    for row in rows:
        match = bzzoiro_row_to_match(row, settings)
        if match is not None and local_date_for(settings, match.commence_time) == local_date:
            matches.append(match)
    stats["matches_built"] = len(matches)
    stats["matches_for_target_date"] = len(matches)
    return matches, stats



async def fetch_sportlogic(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {
        "enabled": env_bool("DAY_INVENTORY_ENABLE_SPORTLOGIC", True),
        "api_key_present": bool(os.getenv("SPORTLOGIC_API_KEY") or os.getenv("SPORTLOGIC_KEY") or os.getenv("SPORTLOGIC_TOKEN") or getattr(settings, "sportlogic_api_key", "")),
        "requests": 0,
        "response_errors": 0,
        "matches_built": 0,
        "matches_for_target_date": 0,
        "source": "sportlogic_provider.fetch_matches",
    }
    if not stats["enabled"] or not stats["api_key_present"]:
        return [], stats
    try:
        from app.providers.sportlogic_provider import SportLogicProvider

        provider = SportLogicProvider(settings)
        matches, provider_stats, _preview = await provider.fetch_matches()
        stats.update({k: v for k, v in dict(provider_stats or {}).items() if k not in {"last_body_preview"}})
        rows: list[Match] = []
        for match in matches or []:
            if getattr(match, "sport_key", "") != "soccer":
                continue
            if local_date_for(settings, match.commence_time) != local_date:
                continue
            meta2 = dict(match.metadata or {})
            source_ids = dict(meta2.get("provider_source_ids") or {})
            if match.source_event_id:
                source_ids["sportlogic"] = str(match.source_event_id)
            meta2["provider_source_ids"] = source_ids
            meta2["sources_seen"] = ",".join(sorted({"sportlogic", *(str(meta2.get("sources_seen") or "").split(","))})).strip(",")
            meta2["core_inventory"] = True
            meta2["sportlogic_fixture_source"] = True
            rows.append(Match(**{**asdict(match), "source": "sportlogic", "metadata": meta2}))
        stats["matches_built"] = len(matches or [])
        stats["matches_for_target_date"] = len(rows)
        return rows, stats
    except Exception as exc:
        stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
        stats["last_error"] = f"{type(exc).__name__}: {exc}"
        return [], stats

def sstats_team(row: dict[str, Any], side: str) -> str:
    candidates = [
        f"{side}Team.name", f"{side}Team.Name", f"{side}.name", f"{side}_team.name",
        f"{side}Name", f"{side}_name", f"{side}Team", f"{side}",
        "Home" if side == "home" else "Away",
        "team1" if side == "home" else "team2",
        "Team1" if side == "home" else "Team2",
        "teamHome.name" if side == "home" else "teamAway.name",
    ]
    value = nested(row, *candidates)
    if isinstance(value, dict):
        value = value.get("name") or value.get("title")
    if not value and isinstance(row.get("teams"), list) and len(row["teams"]) >= 2:
        idx = 0 if side == "home" else 1
        team = row["teams"][idx] if isinstance(row["teams"][idx], dict) else {}
        value = team.get("name") or team.get("Name") or team.get("title")
    return clean_text(value)


def sstats_row_to_match(row: dict[str, Any], settings: Settings) -> Match | None:
    home = sstats_team(row, "home") or clean_text(row.get("homeTeamName"))
    away = sstats_team(row, "away") or clean_text(row.get("awayTeamName"))
    league = nested(row, "league.name", "league.Name", "competition.name", "tournament.name", "leagueName", "LeagueName", "competitionName", "league", "League", "country") or "Unknown"
    start = to_utc(nested(row, "dateTime", "DateTime", "startTime", "StartTime", "start_time", "utcDate", "kickoff", "Kickoff", "date", "Date", "gameTime", "GameTime", "gameDate"))
    if start is None:
        return None
    sid = event_id(row, "id", "gameId", "game_id", "flashId")
    metadata = {
        "provider_source_ids": {"sstats": sid} if sid else {},
        "sources_seen": "sstats",
        "core_inventory": True,
        "sstats_has_context_hint": True,
        "sstats_status": str(row.get("status") or ""),
    }
    if any(k in row for k in ("xg", "homeXg", "awayXg", "homeRating", "awayRating")):
        metadata["sstats_context_fields"] = True
    return make_match(source="sstats", source_event_id=sid, league_name=str(league or "Unknown"), home_team=home, away_team=away, commence_time=start, metadata=metadata)


async def fetch_sstats(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {"enabled": env_bool("DAY_INVENTORY_ENABLE_SSTATS", True), "api_key_present": bool(os.getenv("SSTATS_API_KEY") or getattr(settings, "sstats_api_key", "")), "requests": 0, "response_errors": 0, "rows_fetched": 0, "matches_built": 0, "matches_for_target_date": 0, "http_statuses": [], "endpoint": "Games/list"}
    if not stats["enabled"] or not stats["api_key_present"]:
        return [], stats
    key = str(os.getenv("SSTATS_API_KEY") or getattr(settings, "sstats_api_key", "") or "").strip()
    limit = env_int("DAY_INVENTORY_SSTATS_LIMIT", 1000)
    max_requests = env_int("DAY_INVENTORY_SSTATS_MAX_REQUESTS", 3)
    rows: list[dict[str, Any]] = []
    timeout = float(os.getenv("SSTATS_TIMEOUT_SECONDS") or 25.0)
    headers = {"User-Agent": "HARIZON-day-inventory-sstats/1.0", "Accept": "application/json,text/plain,*/*"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers=headers) as client:
        offset = 0
        while stats["requests"] < max_requests:
            params = {"Date": local_date, "TimeZone": 3, "Limit": limit, "Offset": offset, "Order": 1, "apikey": key}
            try:
                stats["requests"] += 1
                resp = await client.get("https://api.sstats.net/Games/list", params=params)
                stats["http_statuses"].append(resp.status_code)
                if resp.status_code != 200:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = resp.text[:800]
                    break
                payload = resp.json()
                batch = iter_dicts(payload)
                if not batch:
                    break
                rows.extend(batch)
                if len(batch) < limit:
                    break
                offset += len(batch)
            except Exception as exc:
                stats["response_errors"] += 1
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
                break
    stats["rows_fetched"] = len(rows)
    matches: list[Match] = []
    seen_keys: set[str] = set()
    for row in rows:
        match = sstats_row_to_match(row, settings)
        if match is not None and local_date_for(settings, match.commence_time) == local_date:
            key = match.source_event_id or match.match_key
            if key not in seen_keys:
                seen_keys.add(key)
                matches.append(match)
    stats["matches_built"] = len(matches)
    stats["matches_for_target_date"] = len(matches)
    return matches, stats


def merge_matches(matches_by_provider: dict[str, list[Match]], settings: Settings) -> tuple[list[Match], dict[str, Any]]:
    canonical: dict[str, Match] = {}
    quality: dict[str, dict[str, Any]] = {}
    crosswalk: dict[str, Any] = {"matched_rows": [], "unmatched_rows": [], "provider_rows": {k: len(v) for k, v in matches_by_provider.items()}}

    # Seed with odds_api_io first because it has current line ids.
    ordered = ["odds_api_io", "bzzoiro", "sstats", "sportlogic"]
    for provider in ordered:
        for match in matches_by_provider.get(provider, []):
            best_key: str | None = None
            best_score = 0.0
            best_quality = None
            for key, existing in canonical.items():
                try:
                    score, q, _, _ = score_event_match_variants(
                        sport="soccer",
                        match_home=existing.home_team,
                        match_away=existing.away_team,
                        match_start=existing.commence_time,
                        match_league=existing.league_name,
                        event_home_candidates=[match.home_team, match.home_team_norm],
                        event_away_candidates=[match.away_team, match.away_team_norm],
                        event_start=match.commence_time,
                        event_league=match.league_name,
                        exact_tolerance_hours=8,
                        fuzzy_tolerance_hours=18,
                    )
                except Exception:
                    score, q = 0.0, None
                if score > best_score:
                    best_score, best_quality, best_key = score, q, key
            threshold = 66.0 if best_quality != "fuzzy" else 76.0
            if best_key is None or best_score < threshold:
                canonical[match.match_key] = match
                quality[match.match_key] = {"providers": {provider}, "scores": [], "coverage_hints": dict(match.metadata or {})}
                crosswalk["unmatched_rows"].append({"provider": provider, "match_key": match.match_key, "home": match.home_team, "away": match.away_team, "reason": "new_canonical"})
                continue

            existing = canonical[best_key]
            meta = dict(existing.metadata or {})
            incoming = dict(match.metadata or {})
            source_ids = dict(meta.get("provider_source_ids") or {})
            source_ids.update(incoming.get("provider_source_ids") or {})
            if match.source_event_id:
                source_ids[provider] = str(match.source_event_id)
            sources_seen = {x for x in str(meta.get("sources_seen") or existing.source).split(",") if x}
            sources_seen.add(provider)
            meta.update(incoming)
            meta["provider_source_ids"] = source_ids
            meta["sources_seen"] = ",".join(sorted(sources_seen))
            meta["core_inventory"] = True
            canonical[best_key] = Match(**{**asdict(existing), "metadata": meta})
            quality.setdefault(best_key, {"providers": set(), "scores": [], "coverage_hints": {}})
            quality[best_key]["providers"].add(provider)
            quality[best_key]["scores"].append(round(best_score, 2))
            crosswalk["matched_rows"].append({"provider": provider, "provider_match_key": match.match_key, "canonical_match_key": best_key, "score": round(best_score, 2), "quality": best_quality})

    return list(canonical.values()), crosswalk


def priority_score(match: Match, now_utc: datetime) -> float:
    meta = dict(match.metadata or {})
    source_ids = dict(meta.get("provider_source_ids") or {})
    sources = {x for x in str(meta.get("sources_seen") or match.source).split(",") if x}
    sources.update(source_ids.keys())
    hours = (match.commence_time.astimezone(UTC) - now_utc).total_seconds() / 3600.0
    score = 0.0
    if 0 <= hours <= 4:
        score += 130
    elif 4 < hours <= 8:
        score += 115
    elif 8 < hours <= 12:
        score += 100
    elif 12 < hours <= 24:
        score += 80
    else:
        score += 40
    score += min(50, len(sources) * 18)
    if "odds_api_io" in sources:
        score += 30
    if "bzzoiro" in sources:
        score += 18
    if "sstats" in sources:
        score += 18
    if "sportlogic" in sources:
        score += 18
    if meta.get("bzzoiro_has_context_hint") or meta.get("sportlogic_context") or meta.get("sstats_has_context_hint"):
        score += 10
    league = str(match.league_name or "").lower()
    if any(term in league for term in ("premier", "serie a", "la liga", "bundesliga", "ligue 1", "eredivisie", "mls", "championship", "k league", "j1")):
        score += 12
    if str(match.tier).lower() == "low" or is_low_quality_inventory_match(match.league_name, match.home_team, match.away_team):
        score -= 80
    return round(max(0.0, score), 3)


def top_cut(matches: list[Match], max_matches: int) -> list[Match]:
    now = datetime.now(UTC)
    unique = {m.match_key: m for m in matches}
    rows = sorted(unique.values(), key=lambda m: (-priority_score(m, now), m.commence_time.isoformat(), m.league_name, m.home_team))
    return rows[:max_matches]


def enrich_payload_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    counts = Counter()
    for row in matches:
        if not isinstance(row, dict):
            continue
        sources = set(row.get("sources_seen") if isinstance(row.get("sources_seen"), list) else [])
        if isinstance(row.get("source_ids"), dict):
            sources.update(str(k) for k in row["source_ids"].keys())
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        coverage = dict(row.get("coverage") or {})
        has_odds = bool({"odds_api_io", "sportlogic"} & sources) or bool(meta.get("has_current_odds_provider"))
        has_context = bool({"bzzoiro", "sstats"} & sources) or bool(meta.get("bzzoiro_has_context_hint") or meta.get("sstats_has_context_hint"))
        has_xg = bool(meta.get("bzzoiro_context_fields") or meta.get("sstats_context_fields"))
        has_form = "sstats" in sources
        coverage.update({
            "fixture_core": True,
            "odds": has_odds,
            "context": has_context,
            "xg": has_xg,
            "form": has_form,
            "ready_for_model": has_odds and has_context,
            "ready_for_publish": False,
        })
        row["coverage"] = coverage
        row["priority"] = max(as_float(row.get("priority")), float(len(sources) * 20 + (30 if has_odds else 0) + (20 if has_context else 0)))
        counts["matches_with_odds"] += int(has_odds)
        counts["matches_with_context"] += int(has_context)
        counts["matches_with_xg"] += int(has_xg)
        counts["matches_with_form"] += int(has_form)
        counts["matches_ready_for_model"] += int(has_odds and has_context)
        if len(sources & set(CORE_PROVIDERS)) >= 2:
            counts["matches_with_2plus_core_fixture_sources"] += 1
        if len(sources & set(CORE_PROVIDERS)) >= 3:
            counts["matches_with_3_core_fixture_sources"] += 1
    payload.setdefault("counts", {}).update({k: int(v) for k, v in counts.items()})
    payload["matches"] = matches
    return payload


def as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


async def main_async() -> int:
    settings = Settings()
    local_date = target_local_date(settings)
    max_matches = env_int("DAY_INVENTORY_MAX_MATCHES", 300)
    store = DayInventoryStore(timezone_name=str(getattr(settings, "app_timezone", "Europe/Moscow") or "Europe/Moscow"))

    results = await asyncio.gather(
        fetch_odds_api_io(settings, local_date),
        fetch_bzzoiro(settings, local_date),
        fetch_sstats(settings, local_date),
        fetch_sportlogic(settings, local_date),
        return_exceptions=True,
    )
    names = list(CORE_PROVIDERS)
    matches_by_provider: dict[str, list[Match]] = {}
    source_meta: dict[str, Any] = {"mode": "core_fixture_discovery_top300", "providers": list(CORE_PROVIDERS), "attempts": {}}
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            matches_by_provider[name] = []
            source_meta["attempts"][name] = {"stats": {"enabled": True, "response_errors": 1, "last_error": f"{type(result).__name__}: {result}"}}
        else:
            matches, stats = result
            matches_by_provider[name] = matches
            source_meta["attempts"][name] = {"stats": stats}

    merged, crosswalk = merge_matches(matches_by_provider, settings)
    selected = top_cut(merged, max_matches)
    existing = {} if env_bool("DAY_INVENTORY_REBUILD_FROM_SCRATCH", False) else store.load_inventory(local_date)
    payload = store.build_payload(local_date=local_date, matches=selected, source_meta=source_meta, existing=existing)
    payload = enrich_payload_coverage(payload)
    payload.setdefault("counts", {})["matches_after_top_cut"] = len(selected)
    payload["counts"]["target_matches"] = max_matches
    payload["counts"]["target_shortfall"] = max(0, max_matches - len(selected))
    payload["counts"]["target_full"] = len(selected) >= max_matches
    payload["counts"]["matches_raw_before_merge"] = sum(len(v) for v in matches_by_provider.values())
    payload["counts"]["matches_after_core_merge"] = len(merged)
    payload["core_crosswalk"] = {
        "provider_rows": crosswalk.get("provider_rows"),
        "matched_rows_count": len(crosswalk.get("matched_rows") or []),
        "unmatched_rows_count": len(crosswalk.get("unmatched_rows") or []),
    }
    paths = store.save_inventory(payload)

    report = {
        "date_local": local_date,
        "build_status": "ok",
        "mode": "core_fixture_discovery_top300",
        "max_matches": max_matches,
        "target_matches": max_matches,
        "target_shortfall": max(0, max_matches - len(selected)),
        "target_full": len(selected) >= max_matches,
        "inventory_policy": "merge-only top-300 core fixture inventory from odds-api.io, Bzzoiro, SStats and quota-governed SportLogic; deep detail endpoints remain runtime shortlist enrichment",
        "providers": {name: dict(source_meta["attempts"].get(name, {}).get("stats") or {}) for name in CORE_PROVIDERS},
        "counts": dict(payload.get("counts") or {}),
        "source_match_counts": dict(payload.get("source_match_counts") or {}),
        "all_source_match_counts": dict(payload.get("all_source_match_counts") or {}),
        "saved_paths": paths,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    CORE_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CROSSWALK_PATH.write_text(json.dumps(crosswalk, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    timeout = env_int("DAY_INVENTORY_BUILD_TIMEOUT_SECONDS", 900)
    try:
        return asyncio.run(asyncio.wait_for(main_async(), timeout=timeout))
    except TimeoutError:
        payload = {"build_status": "timeout_guarded", "error": f"timeout_after_{timeout}s", "created_at_utc": datetime.now(UTC).isoformat()}
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
