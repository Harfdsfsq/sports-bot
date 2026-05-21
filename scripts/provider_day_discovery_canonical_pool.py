from __future__ import annotations

"""Provider day discovery canonical pool.

Prototype for the improved architecture:
1. Ask every fixture-capable provider for today's matches.
2. Normalize home/away/league/kickoff into a canonical event pool.
3. Merge provider rows into canonical matches and preserve provider source IDs.
4. Build a targeted enrichment plan: primary providers first, supplemental APIs
   only after canonical matches/top candidates exist.

This script is diagnostic and provider-smoke safe. It does not publish picks and
it does not mutate the production inventory. The output tells us whether the
next production step should replace the current odds-first inventory builder.
"""

import asyncio
import json
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.json"
TXT_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.txt"
UA = "HARIZON-provider-day-discovery-canonical-pool/1.0"

PRIMARY_PROVIDERS = {"odds_api_io", "bzzoiro", "sstats"}
FIXTURE_PROVIDERS = {"odds_api_io", "bzzoiro", "sstats", "football_data", "thesportsdb", "allsportsapi", "highlightly", "sportlogic"}
SUPPLEMENTAL_PROVIDERS = {"clubelo", "open_meteo", "weatherapi", "openweathermap", "newsapi", "currents", "guardian", "newsdata", "wikidata"}
STOPWORDS = {
    "fc", "cf", "sc", "afc", "club", "football", "soccer", "the", "fk", "sk", "ac", "as", "cd", "sd", "de", "la",
    "women", "woman", "w", "u17", "u18", "u19", "u20", "u21", "u23", "reserves", "reserve", "ii", "b",
}


@dataclass(frozen=True)
class CallSpec:
    provider: str
    command: str
    url: str
    role: str
    params: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def first_env(*names: str) -> tuple[str, str]:
    for name in names:
        value = env(name)
        if value:
            return name, value
    return "", ""


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def target_date() -> str:
    raw = env("DAY_INVENTORY_TARGET_DATE") or env("PROVIDER_SMOKE_TARGET_DATE")
    if raw:
        return raw[:10]
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def date_plus(date_text: str, days: int) -> str:
    try:
        return (datetime.fromisoformat(date_text[:10]).date() + timedelta(days=days)).isoformat()
    except Exception:
        return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.replace(tzinfo=UTC)
        except Exception:
            continue
    return None


def normalize(text: Any) -> str:
    raw = str(text or "").lower().strip()
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    tokens = [tok for tok in raw.split() if tok and tok not in STOPWORDS]
    return " ".join(tokens)


def sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return max(0.88, SequenceMatcher(None, a, b).ratio())
    aset, bset = set(a.split()), set(b.split())
    ratio = SequenceMatcher(None, a, b).ratio()
    jaccard = len(aset & bset) / max(1, len(aset | bset))
    return max(ratio, jaccard)


def dig(row: Any, key: str) -> Any:
    cur = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def as_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "Name", "short_name", "shortName", "display_name", "displayName", "title", "Title"):
            if value.get(key) not in (None, ""):
                return str(value.get(key)).strip()
        return ""
    return str(value or "").strip()


def first(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        got = as_name(dig(row, key) if "." in key else row.get(key))
        if got:
            return got
    return ""


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "result", "response", "events", "matches", "fixtures", "games", "items", "predictions"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    return [payload] if payload else []


def source_id(row: dict[str, Any], provider: str) -> str:
    keys = {
        "odds_api_io": ("id", "event_id", "eventId"),
        "bzzoiro": ("id", "event_id", "eventId", "game_id", "fixture_id", "match_id"),
        "sstats": ("id", "Id", "gameId", "GameId"),
        "football_data": ("id",),
        "thesportsdb": ("idEvent", "id", "event_id"),
        "allsportsapi": ("event_key", "id", "fixture_id"),
        "highlightly": ("id", "fixture.id", "match.id"),
        "sportlogic": ("id", "game_id", "event_id"),
    }.get(provider, ("id", "game_id", "fixture_id", "match_id", "event_id"))
    for key in keys:
        value = dig(row, key) if "." in key else row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def extract_event(row: Any, provider: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    source = row.get("event") if isinstance(row.get("event"), dict) else row
    home = away = league = start_raw = ""
    if provider == "odds_api_io":
        home = first(source, ("home", "home_team", "homeTeam", "home.name"))
        away = first(source, ("away", "away_team", "awayTeam", "away.name"))
        start_raw = first(source, ("date", "commence_time", "start_time", "starts_at"))
        league = first(source, ("league", "competition", "sport", "tournament.name"))
    elif provider == "sstats":
        home = first(source, ("homeTeam.name", "homeTeam.Name", "homeTeamName", "home_team", "home", "Home", "homeName", "team1", "Team1"))
        away = first(source, ("awayTeam.name", "awayTeam.Name", "awayTeamName", "away_team", "away", "Away", "awayName", "team2", "Team2"))
        league = first(source, ("league.name", "league.Name", "leagueName", "LeagueName", "league", "League", "competition.name"))
        start_raw = first(source, ("dateTime", "DateTime", "startTime", "StartTime", "kickoff", "Kickoff", "date", "Date", "gameTime", "GameTime"))
    elif provider == "bzzoiro":
        home = first(source, ("home_team", "home_team.name", "homeTeam.name", "home.name", "home", "homeTeam"))
        away = first(source, ("away_team", "away_team.name", "awayTeam.name", "away.name", "away", "awayTeam"))
        league = first(source, ("league.name", "competition.name", "tournament.name", "league", "competition"))
        start_raw = first(source, ("event_date", "date", "start_time", "starts_at", "start", "kickoff", "datetime"))
    elif provider == "football_data":
        home = first(source, ("homeTeam.name", "homeTeam.shortName"))
        away = first(source, ("awayTeam.name", "awayTeam.shortName"))
        league = first(source, ("competition.name", "area.name"))
        start_raw = first(source, ("utcDate",))
    elif provider == "thesportsdb":
        home = first(source, ("strHomeTeam", "homeTeam", "home"))
        away = first(source, ("strAwayTeam", "awayTeam", "away"))
        league = first(source, ("strLeague", "strCountry", "league"))
        start_raw = first(source, ("strTimestamp", "dateEvent", "dateEventLocal"))
        if start_raw and "T" not in start_raw:
            time_raw = first(source, ("strTime", "strTimeLocal")) or "00:00:00"
            start_raw = f"{start_raw}T{time_raw}"
    elif provider == "allsportsapi":
        home = first(source, ("event_home_team", "home_team", "home"))
        away = first(source, ("event_away_team", "away_team", "away"))
        league = first(source, ("league_name", "country_name", "league"))
        date_raw = first(source, ("event_date", "date"))
        time_raw = first(source, ("event_time", "time")) or "00:00:00"
        start_raw = f"{date_raw}T{time_raw}" if date_raw else ""
    elif provider == "highlightly":
        home = first(source, ("homeTeam.name", "home.name", "home_team", "home"))
        away = first(source, ("awayTeam.name", "away.name", "away_team", "away"))
        league = first(source, ("league.name", "competition.name", "tournament.name"))
        start_raw = first(source, ("date", "startTime", "startsAt", "kickoff"))
    elif provider == "sportlogic":
        home = first(source, ("home_team", "home.name", "homeTeam.name", "home"))
        away = first(source, ("away_team", "away.name", "awayTeam.name", "away"))
        league = first(source, ("league.name", "competition.name", "league", "sport"))
        start_raw = first(source, ("start_time", "starts_at", "kickoff", "date"))
    else:
        home = first(source, ("home_team", "homeTeam.name", "home.name", "home"))
        away = first(source, ("away_team", "awayTeam.name", "away.name", "away"))
        league = first(source, ("league.name", "competition.name", "league", "competition"))
        start_raw = first(source, ("start_time", "starts_at", "start", "date", "kickoff"))
    if not home or not away:
        return None
    start_dt = parse_dt(start_raw)
    return {
        "provider": provider,
        "source_id": source_id(source, provider),
        "home_team": home,
        "away_team": away,
        "league_name": league,
        "kickoff_utc": start_dt.isoformat() if start_dt else None,
        "home_norm": normalize(home),
        "away_norm": normalize(away),
        "league_norm": normalize(league),
        "raw_keys": sorted(str(k) for k in source.keys())[:30],
    }


def match_score(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    home = sim(str(a.get("home_norm") or ""), str(b.get("home_norm") or ""))
    away = sim(str(a.get("away_norm") or ""), str(b.get("away_norm") or ""))
    swapped = (sim(str(a.get("home_norm") or ""), str(b.get("away_norm") or "")) + sim(str(a.get("away_norm") or ""), str(b.get("home_norm") or ""))) / 2.0
    pair = (home + away) / 2.0
    league = sim(str(a.get("league_norm") or ""), str(b.get("league_norm") or ""))
    adt, bdt = parse_dt(a.get("kickoff_utc")), parse_dt(b.get("kickoff_utc"))
    delta = None
    time_score = 0.02
    if adt and bdt:
        delta = abs((adt - bdt).total_seconds()) / 60.0
        if delta <= 15:
            time_score = 0.16
        elif delta <= 60:
            time_score = 0.10
        elif delta <= 180:
            time_score = 0.04
        elif delta > 720:
            time_score = -0.18
    score = pair * 0.78 + league * 0.08 + time_score
    if swapped > pair + 0.08:
        score -= 0.25
    return max(0.0, min(1.0, score)), {"home": round(home, 3), "away": round(away, 3), "league": round(league, 3), "pair": round(pair, 3), "swapped": round(swapped, 3), "delta_minutes": round(delta, 1) if delta is not None else None}


def canonical_key(event: dict[str, Any]) -> str:
    dt = parse_dt(event.get("kickoff_utc"))
    date_part = dt.date().isoformat() if dt else target_date()
    return f"{date_part}|{event.get('home_norm')}|{event.get('away_norm')}"


def merge_events(events: list[dict[str, Any]], min_score: float = 0.74) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for event in events:
        best_idx = -1
        best_score = 0.0
        best_debug: dict[str, Any] = {}
        for idx, current in enumerate(canonical):
            score, debug = match_score(current, event)
            if score > best_score:
                best_idx, best_score, best_debug = idx, score, debug
        if best_idx >= 0 and best_score >= min_score:
            cur = canonical[best_idx]
            cur["providers"] = sorted(set(cur.get("providers", [])) | {str(event.get("provider"))})
            cur.setdefault("source_ids", {})[str(event.get("provider"))] = event.get("source_id")
            cur.setdefault("source_events", []).append(event)
            cur.setdefault("merge_debug", []).append({"provider": event.get("provider"), "score": round(best_score, 4), "debug": best_debug})
            if not cur.get("kickoff_utc") and event.get("kickoff_utc"):
                cur["kickoff_utc"] = event.get("kickoff_utc")
            if not cur.get("league_name") and event.get("league_name"):
                cur["league_name"] = event.get("league_name")
        else:
            canonical.append({
                "canonical_match_key": canonical_key(event),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "league_name": event.get("league_name"),
                "kickoff_utc": event.get("kickoff_utc"),
                "home_norm": event.get("home_norm"),
                "away_norm": event.get("away_norm"),
                "league_norm": event.get("league_norm"),
                "providers": [event.get("provider")],
                "source_ids": {str(event.get("provider")): event.get("source_id")},
                "source_events": [event],
                "merge_debug": [],
            })
    return canonical


def build_calls() -> list[CallSpec]:
    t = target_date()
    tm = date_plus(t, 1)
    calls: list[CallSpec] = []
    _, odds = first_env("ODDS_API_IO_KEY")
    _, odds2 = first_env("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    odds_limit = max(20, min(100, as_int(env("PROVIDER_DAY_DISCOVERY_ODDS_API_IO_PAGE_LIMIT"), 100)))
    odds_target = max(300, as_int(env("DAY_INVENTORY_TARGET_SIZE") or env("DAY_INVENTORY_MAX_MATCHES"), 300))
    default_pages = max(1, (odds_target + odds_limit - 1) // odds_limit)
    max_config_pages = as_int(env("PROVIDER_DAY_DISCOVERY_ODDS_API_IO_PAGES") or env("ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT"), default_pages)
    odds_pages = max(1, min(max_config_pages, default_pages, 6))
    if odds:
        for page in range(1, odds_pages + 1):
            calls.append(CallSpec("odds_api_io", f"events_account1_page_{page}", "https://api.odds-api.io/v3/events", "fixture_primary_odds", {"apiKey": odds, "sport": "football", "status": "pending,live", "from": f"{t}T00:00:00Z", "to": f"{tm}T00:00:00Z", "limit": odds_limit, "page": page}))
    if odds2:
        for page in range(1, odds_pages + 1):
            calls.append(CallSpec("odds_api_io", f"events_account2_page_{page}", "https://api.odds-api.io/v3/events", "fixture_primary_odds", {"apiKey": odds2, "sport": "football", "status": "pending,live", "from": f"{t}T00:00:00Z", "to": f"{tm}T00:00:00Z", "limit": odds_limit, "page": page}))
    _, bzz = first_env("BZZOIRO_API_KEY")
    if bzz:
        h = {"Authorization": f"Token {bzz}"}
        calls.extend([
            CallSpec("bzzoiro", "events_v1_day", "https://sports.bzzoiro.com/api/events/", "fixture_primary_context", {"date_from": t, "date_to": tm, "tz": "UTC", "limit": 100}, h),
            CallSpec("bzzoiro", "predictions_v1_day", "https://sports.bzzoiro.com/api/predictions/", "fixture_primary_prediction", {"date_from": t, "date_to": tm, "upcoming": "true", "tz": "UTC", "limit": 100}, h),
            CallSpec("bzzoiro", "events_v2_day_offset_0", "https://sports.bzzoiro.com/api/v2/events/", "fixture_primary_context", {"date_from": t, "date_to": tm, "limit": 100, "offset": 0}, h),
        ])
    _, sstats = first_env("SSTATS_API_KEY")
    if sstats:
        calls.extend([
            CallSpec("sstats", "games_date", "https://api.sstats.net/Games/list", "fixture_primary_context", {"Date": t, "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1, "apikey": sstats}),
            CallSpec("sstats", "games_upcoming", "https://api.sstats.net/Games/list", "fixture_primary_context", {"Upcoming": "true", "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1, "apikey": sstats}),
        ])
    _, fd = first_env("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY")
    if fd:
        calls.append(CallSpec("football_data", "matches_day", "https://api.football-data.org/v4/matches", "fixture_supplemental_mapping", {"dateFrom": t, "dateTo": tm, "status": "SCHEDULED,TIMED,IN_PLAY,PAUSED"}, {"X-Auth-Token": fd}))
    tsdb = env("THESPORTSDB_API_KEY", "123") or "123"
    calls.append(CallSpec("thesportsdb", "eventsday_soccer", f"https://www.thesportsdb.com/api/v1/json/{tsdb}/eventsday.php", "fixture_supplemental_mapping", {"d": t, "s": "Soccer"}))
    _, allsport = first_env("ALLSPORTSAPI_API_KEY")
    if allsport:
        calls.append(CallSpec("allsportsapi", "fixtures_day", "https://apiv2.allsportsapi.com/football/", "fixture_supplemental_mapping", {"met": "Fixtures", "APIkey": allsport, "from": t, "to": tm, "timezone": "UTC"}))
    _, hl = first_env("HIGHLIGHTLY_API_KEY", "HIGHLIGHTLY_KEY")
    base_url = env("HIGHLIGHTLY_BASE_URL")
    if hl and base_url:
        calls.append(CallSpec("highlightly", "matches_day", base_url.rstrip("/") + "/matches", "fixture_supplemental_mapping", {"date": t}, {"x-api-key": hl}))
    # SportLogic is intentionally not broad by default because current free quota is small.
    if env("PROVIDER_DAY_DISCOVERY_INCLUDE_SPORTLOGIC", "false").lower() in {"1", "true", "yes", "on"}:
        _, sl = first_env("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
        if sl:
            root = env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
            calls.append(CallSpec("sportlogic", "games_day", f"{root}/games", "fixture_supplemental_mapping", {"date_from": t, "date_to": tm, "per_page": 100}, {env("SPORTLOGIC_HEADER_NAME", "X-API-Key") or "X-API-Key": sl}))
    return calls


async def call(client: httpx.AsyncClient, spec: CallSpec) -> dict[str, Any]:
    started = time.perf_counter()
    payload: Any = None
    error = ""
    http_status: int | None = None
    try:
        headers = {"User-Agent": UA, "Accept": "application/json"}
        headers.update(spec.headers or {})
        response = await client.get(spec.url, params=spec.params or None, headers=headers)
        http_status = response.status_code
        try:
            payload = response.json()
        except Exception:
            payload = response.text
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    rs = rows(payload)
    events = [event for row in rs if (event := extract_event(row, spec.provider))]
    if error:
        status = "ERROR"
    elif http_status == 429:
        status = "RATE_LIMIT"
    elif http_status in {401, 403}:
        status = "AUTH"
    elif http_status and 200 <= http_status < 300:
        status = "OK" if rs else "EMPTY"
    else:
        status = "HTTP_ERROR"
    return {"provider": spec.provider, "command": spec.command, "role": spec.role, "status": status, "http_status": http_status, "duration_ms": round((time.perf_counter() - started) * 1000, 1), "rows_count": len(rs), "event_like_rows": len(events), "events": events[:1000], "error": error}


def enrichment_plan(canonical: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in canonical[:300]:
        providers = set(str(p) for p in item.get("providers") or [])
        source_ids = item.get("source_ids") if isinstance(item.get("source_ids"), dict) else {}
        actions: list[str] = []
        if "odds_api_io" in providers:
            actions.append("odds-api.io: odds/multi by odds event id, both accounts/bookmaker groups")
        if "bzzoiro" in providers:
            actions.append("Bzzoiro: event detail/stats/odds/metadata/lineups/prediction by bzzoiro event id")
        if "sstats" in providers:
            actions.append("SStats: /Games/glicko/{id}, /Games/last-games-stats, /Games/{id}; /Odds/{gameId} only if odds<2")
        if len(providers & PRIMARY_PROVIDERS) < 2:
            actions.append("Primary repair: try alias crosswalk between odds-api.io/Bzzoiro/SStats before supplemental APIs")
        actions.append("Supplemental after shortlist only: ClubElo rating, venue->Open-Meteo/WeatherAPI, news by team aliases, TheSportsDB/Wikidata mapping")
        out.append({"canonical_match_key": item.get("canonical_match_key"), "home_team": item.get("home_team"), "away_team": item.get("away_team"), "kickoff_utc": item.get("kickoff_utc"), "providers": sorted(providers), "source_ids": source_ids, "provider_count": len(providers), "primary_provider_count": len(providers & PRIMARY_PROVIDERS), "actions": actions})
    return out


def summarize(results: list[dict[str, Any]], canonical: list[dict[str, Any]]) -> dict[str, Any]:
    provider_rows: dict[str, Any] = {}
    for row in results:
        provider = str(row.get("provider") or "unknown")
        cur = provider_rows.setdefault(provider, {"commands": 0, "ok": 0, "rows": 0, "events": 0, "statuses": {}})
        cur["commands"] += 1
        cur["ok"] += 1 if row.get("status") == "OK" else 0
        cur["rows"] += as_int(row.get("rows_count"), 0)
        cur["events"] += as_int(row.get("event_like_rows"), 0)
        cur["statuses"][str(row.get("status") or "unknown")] = cur["statuses"].get(str(row.get("status") or "unknown"), 0) + 1
    source_dist = Counter(len(item.get("providers") or []) for item in canonical)
    primary_dist = Counter(len(set(item.get("providers") or []) & PRIMARY_PROVIDERS) for item in canonical)
    return {"provider_rows": provider_rows, "canonical_matches": len(canonical), "source_count_distribution": dict(source_dist), "primary_source_count_distribution": dict(primary_dist), "canonical_with_2plus_sources": sum(1 for item in canonical if len(item.get("providers") or []) >= 2), "canonical_with_2plus_primary_sources": sum(1 for item in canonical if len(set(item.get("providers") or []) & PRIMARY_PROVIDERS) >= 2), "canonical_with_all_3_primary_sources": sum(1 for item in canonical if PRIMARY_PROVIDERS.issubset(set(item.get("providers") or [])))}


def render(payload: dict[str, Any]) -> str:
    s = payload.get("summary") or {}
    lines = ["# Provider day discovery canonical pool", f"UTC: {payload.get('created_at_utc')}", f"target_date: {payload.get('target_date')}", f"canonical_matches: {s.get('canonical_matches', 0)}", f"2+ provider sources: {s.get('canonical_with_2plus_sources', 0)}", f"2+ primary sources: {s.get('canonical_with_2plus_primary_sources', 0)}", f"all 3 primary sources: {s.get('canonical_with_all_3_primary_sources', 0)}", "", "## Provider fixture discovery"]
    for provider, row in sorted((s.get("provider_rows") or {}).items()):
        lines.append(f"- {provider}: commands={row.get('commands')} ok={row.get('ok')} rows={row.get('rows')} event_like={row.get('events')} statuses={json.dumps(row.get('statuses') or {}, ensure_ascii=False)}")
    lines += ["", "## Source distributions", f"- all providers: {json.dumps(s.get('source_count_distribution') or {}, ensure_ascii=False)}", f"- primary providers: {json.dumps(s.get('primary_source_count_distribution') or {}, ensure_ascii=False)}", "", "## Top canonical matches"]
    for item in (payload.get("canonical_matches_sample") or [])[:30]:
        lines.append(f"- {item.get('kickoff_utc')} | {item.get('home_team')} — {item.get('away_team')} | providers={','.join(item.get('providers') or [])} ids={json.dumps(item.get('source_ids') or {}, ensure_ascii=False)}")
    lines += ["", "## Targeted enrichment plan sample"]
    for item in (payload.get("targeted_enrichment_plan") or [])[:20]:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | primary={item.get('primary_provider_count')} providers={','.join(item.get('providers') or [])}")
        for action in item.get("actions") or []:
            lines.append(f"  - {action}")
    return "\n".join(lines) + "\n"


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    max_seconds = float(env("PROVIDER_DAY_DISCOVERY_MAX_SECONDS", "140"))
    timeout = float(env("PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS", "18"))
    concurrency = max(1, as_int(env("PROVIDER_DAY_DISCOVERY_CONCURRENCY"), 5))
    specs = build_calls()
    sem = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True) as client:
        async def guarded(spec: CallSpec) -> dict[str, Any]:
            async with sem:
                return await call(client, spec)
        try:
            results = await asyncio.wait_for(asyncio.gather(*(guarded(spec) for spec in specs)), timeout=max_seconds)
        except asyncio.TimeoutError:
            results = []
    events = [event for result in results for event in (result.get("events") or []) if isinstance(event, dict)]
    canonical = merge_events(events, min_score=float(env("PROVIDER_DAY_DISCOVERY_MIN_SCORE", "0.74")))
    canonical = sorted(canonical, key=lambda item: (str(item.get("kickoff_utc") or ""), -len(item.get("providers") or [])))
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "provider_day_discovery_canonical_pool_v1", "status": "ok", "target_date": target_date(), "duration_seconds": round(time.perf_counter() - started, 2), "summary": summarize(results, canonical), "results_summary": [{k: v for k, v in result.items() if k != "events"} for result in results], "canonical_matches_sample": canonical[:80], "targeted_enrichment_plan": enrichment_plan(canonical), "notes": ["Discovery first: fixture-capable providers are queried before per-match enrichment.", "Canonical source_ids make targeted odds/context requests possible without fuzzy matching every detail endpoint.", "Supplemental APIs should be called only after this pool is shortlisted by model/coverage needs."]}
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
