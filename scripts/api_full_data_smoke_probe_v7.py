from __future__ import annotations

"""Broad full-data endpoint probe for provider-smoke.

Runs cheap endpoint checks across all configured providers and writes a compact
JSON/TXT report showing which commands return rows and matchable home/away data.
"""

import asyncio
import csv
import io
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-api-full-data-enrichment.json"
TXT_OUT = OUT_DIR / "latest-api-full-data-enrichment.txt"
UA = "HARIZON-provider-smoke-full-data-v7"


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


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def tomorrow() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def yesterday() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_plus(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def safe(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            low = str(key).lower()
            if any(token in low for token in ("key", "token", "authorization")):
                out[str(key)] = "***"
            else:
                out[str(key)] = safe(item, depth + 1)
        return out
    if isinstance(value, list):
        return [safe(item, depth + 1) for item in value[:5]]
    if isinstance(value, str):
        return value[:800]
    return value


def shape(value: Any) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return ",".join(sorted(str(k) for k in value.keys())[:12])
    if isinstance(value, str):
        return "text"
    return type(value).__name__


def csv_rows(text: str) -> list[Any]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        if "," in raw.splitlines()[0]:
            return list(csv.DictReader(io.StringIO(raw)))[:50]
    except Exception:
        pass
    return raw.splitlines()[:50]


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, str):
        return csv_rows(payload)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "result", "response", "events", "matches", "fixtures", "games", "items", "teams", "leagues", "competitions", "standings", "articles", "news", "bookmakers", "odds", "markets", "predictions"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    return [payload] if payload else []


def dig(row: Any, *path: str) -> Any:
    cur = row
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "short_name", "shortName", "display_name", "displayName"):
            if value.get(key) not in (None, ""):
                return str(value.get(key)).strip()
        return ""
    return str(value or "").strip()


def first(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = dig(row, *key.split(".")) if "." in key else row.get(key)
        got = text(value)
        if got:
            return got
    return ""


def event_like(row: Any, provider: str) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    source = row.get("event") if isinstance(row.get("event"), dict) else row
    if provider == "football_data":
        home = first(source, ("homeTeam.name", "homeTeam.shortName")); away = first(source, ("awayTeam.name", "awayTeam.shortName")); start = str(source.get("utcDate") or ""); sid = str(source.get("id") or "")
    elif provider == "allsportsapi":
        home = first(source, ("event_home_team", "home_team", "home")); away = first(source, ("event_away_team", "away_team", "away")); start = f"{source.get('event_date') or ''}T{source.get('event_time') or '00:00'}:00+00:00" if source.get("event_date") else ""; sid = str(source.get("event_key") or source.get("id") or "")
    elif provider == "odds_api_io":
        home = first(source, ("home", "home_team", "homeTeam")); away = first(source, ("away", "away_team", "awayTeam")); start = str(source.get("date") or source.get("commence_time") or ""); sid = str(source.get("id") or "")
    else:
        home = first(source, ("home_team", "home_team_obj.name", "homeTeam.name", "home.name", "home")); away = first(source, ("away_team", "away_team_obj.name", "awayTeam.name", "away.name", "away")); start = str(source.get("event_date") or source.get("date") or source.get("start_time") or source.get("starts_at") or source.get("start") or ""); sid = str(source.get("id") or source.get("game_id") or source.get("fixture_id") or source.get("match_id") or "")
    if not home or not away:
        return None
    league = first(source, ("league.name", "competition.name", "tournament.name", "league", "competition"))
    return {"home": home, "away": away, "league": league, "start": start, "source_id": sid}


def quality(provider: str, rs: list[Any]) -> dict[str, Any]:
    evs = []
    keys = Counter()
    for row in rs[:60]:
        if isinstance(row, dict):
            keys.update(str(k) for k in row.keys())
            ev = event_like(row, provider)
            if ev:
                evs.append(ev)
    return {"rows_count": len(rs), "event_like_rows": len(evs), "usable_for_matching": bool(evs), "sample_event": evs[0] if evs else None, "top_keys": [k for k, _ in keys.most_common(20)]}


async def call(client: httpx.AsyncClient, spec: CallSpec) -> dict[str, Any]:
    started = time.perf_counter()
    payload: Any = None
    body = ""
    http_status: int | None = None
    error = ""
    try:
        h = {"User-Agent": UA, "Accept": "application/json"}
        h.update(spec.headers or {})
        response = await client.get(spec.url, params=spec.params or None, headers=h)
        http_status = response.status_code
        body = response.text[:1200]
        try:
            payload = response.json()
        except Exception:
            payload = response.text
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    rs = rows(payload)
    if error:
        status = "TIMEOUT" if "timeout" in error.lower() else "ERROR"
    elif http_status == 429:
        status = "RATE_LIMIT"
    elif http_status in {401, 403}:
        status = "AUTH"
    elif http_status and 200 <= http_status < 300:
        status = "OK" if rs else "EMPTY"
    else:
        status = "HTTP_ERROR"
    return {"provider": spec.provider, "command": spec.command, "role": spec.role, "status": status, "http_status": http_status, "duration_ms": round((time.perf_counter() - started) * 1000, 1), "rows_count": len(rs), "payload_shape": shape(payload), "quality": quality(spec.provider, rs), "sample": safe(rs[:5]), "body_preview": safe(body), "error": error, "url": spec.url, "params": safe(spec.params or {})}


def build_calls() -> list[CallSpec]:
    t, tm, y = today(), tomorrow(), yesterday()
    calls: list[CallSpec] = []
    _, odds = first_env("ODDS_API_IO_KEY")
    _, odds2 = first_env("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    if odds:
        calls.append(CallSpec("odds_api_io", "events_account1", "https://api.odds-api.io/v3/events", "fixture_inventory", {"apiKey": odds, "sport": "football", "status": "pending,live", "from": iso_now(), "to": iso_plus(1), "limit": 30, "page": 1}))
    if odds2:
        calls.append(CallSpec("odds_api_io", "events_account2", "https://api.odds-api.io/v3/events", "fixture_inventory", {"apiKey": odds2, "sport": "football", "status": "pending,live", "from": iso_now(), "to": iso_plus(1), "limit": 10, "page": 1}))
    _, bzz = first_env("BZZOIRO_API_KEY")
    if bzz:
        h = {"Authorization": f"Token {bzz}"}
        calls += [
            CallSpec("bzzoiro", "v1_events", "https://sports.bzzoiro.com/api/events/", "fixture_context", {"date_from": t, "date_to": tm, "tz": "UTC", "limit": 25}, h),
            CallSpec("bzzoiro", "v1_predictions", "https://sports.bzzoiro.com/api/predictions/", "prediction_context", {"date_from": t, "date_to": tm, "upcoming": "true", "tz": "UTC", "limit": 25}, h),
            CallSpec("bzzoiro", "v2_events", "https://sports.bzzoiro.com/api/v2/events/", "fixture_context", {"date_from": t, "date_to": tm, "limit": 25, "offset": 0}, h),
            CallSpec("bzzoiro", "v2_live_events", "https://sports.bzzoiro.com/api/v2/events/live/", "live_context", {"limit": 25, "offset": 0}, h),
        ]
    _, sstats = first_env("SSTATS_API_KEY")
    if sstats:
        calls += [CallSpec("sstats", "games_recent", "https://api.sstats.net/Games/list", "historical_form", {"from": y, "to": t, "limit": 100, "offset": 0, "apikey": sstats}), CallSpec("sstats", "games_today_window", "https://api.sstats.net/Games/list", "fixture_or_live_context", {"from": t, "to": tm, "limit": 100, "offset": 0, "apikey": sstats})]
    _, fd = first_env("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY")
    if fd:
        h = {"X-Auth-Token": fd}
        calls += [CallSpec("football_data", "matches_scheduled", "https://api.football-data.org/v4/matches", "fixture_context", {"dateFrom": t, "dateTo": tm, "status": "SCHEDULED,TIMED"}, h), CallSpec("football_data", "competitions", "https://api.football-data.org/v4/competitions", "mapping", {}, h), CallSpec("football_data", "standings_PL", "https://api.football-data.org/v4/competitions/PL/standings", "standings_context", {}, h)]
    tsdb = env("THESPORTSDB_API_KEY", "123") or "123"
    base = f"https://www.thesportsdb.com/api/v1/json/{tsdb}"
    calls += [CallSpec("thesportsdb", "eventsday_soccer", f"{base}/eventsday.php", "fixture_context", {"d": t, "s": "Soccer"}), CallSpec("thesportsdb", "all_leagues", f"{base}/all_leagues.php", "mapping", {}), CallSpec("thesportsdb", "search_arsenal", f"{base}/searchteams.php", "team_mapping", {"t": "Arsenal"})]
    _, asapi = first_env("ALLSPORTSAPI_API_KEY")
    if asapi:
        calls += [CallSpec("allsportsapi", "fixtures", "https://apiv2.allsportsapi.com/football/", "fixture_context", {"met": "Fixtures", "APIkey": asapi, "from": t, "to": tm, "timezone": "UTC"}), CallSpec("allsportsapi", "leagues", "https://apiv2.allsportsapi.com/football/", "mapping", {"met": "Leagues", "APIkey": asapi})]
    _, sl = first_env("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
    if sl:
        root = env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
        h = {(env("SPORTLOGIC_HEADER_NAME", "X-API-Key") or "X-API-Key"): sl}
        calls += [CallSpec("sportlogic", "games_date_from_to", f"{root}/games", "fixture_context", {"date_from": t, "date_to": tm, "per_page": 50}, h), CallSpec("sportlogic", "games_status_scheduled", f"{root}/games", "fixture_context", {"date_from": t, "date_to": tm, "status": "scheduled", "per_page": 50}, h), CallSpec("sportlogic", "active_odds", f"{root}/odds", "odds_discovery", {"is_active": "true", "per_page": 50}, h)]
    _, wx = first_env("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY")
    if wx:
        calls += [CallSpec("weatherapi", "current_london", "https://api.weatherapi.com/v1/current.json", "weather", {"key": wx, "q": "London", "aqi": "no"}), CallSpec("weatherapi", "forecast_london", "https://api.weatherapi.com/v1/forecast.json", "weather", {"key": wx, "q": "London", "days": 2, "aqi": "no", "alerts": "no"})]
    calls += [CallSpec("open_meteo", "forecast_london", "https://api.open-meteo.com/v1/forecast", "weather", {"latitude": 51.5072, "longitude": -0.1276, "hourly": "temperature_2m,precipitation,wind_speed_10m", "forecast_days": 2}), CallSpec("clubelo", "today_csv", f"{env('CLUBELO_BASE_URL', 'http://api.clubelo.com').rstrip('/')}/{t}", "elo_mapping", {}), CallSpec("clubelo", "team_arsenal", f"{env('CLUBELO_BASE_URL', 'http://api.clubelo.com').rstrip('/')}/Arsenal", "elo_team_history", {"from": y, "to": t})]
    for provider, names, url, param_name in [("newsapi", ("NEWSAPI_KEY",), "https://newsapi.org/v2/everything", "apiKey"), ("currents", ("CURRENTS_API_KEY", "CURRENTS_KEY"), "https://api.currentsapi.services/v1/latest-news", "apiKey"), ("gnews", ("GNEWS_KEY",), "https://gnews.io/api/v4/search", "token"), ("newsdata", ("NEWSDATA_API_KEY", "NEWSDATA_KEY"), "https://newsdata.io/api/1/news", "apikey"), ("guardian", ("GUARDIAN_API_KEY", "GUARDIAN_KEY"), "https://content.guardianapis.com/search", "api-key")]:
        _, key = first_env(*names)
        if key:
            params = {param_name: key, "q": "football injury lineup"}
            if provider in {"currents", "newsdata"}:
                params = {param_name: key, "category": "sports", "language": "en", "q": "football"}
            calls.append(CallSpec(provider, "news_context", url, "news_context", params))
    return calls


def ids_from(result: dict[str, Any], provider: str) -> list[str]:
    out = []
    for row in result.get("sample") or []:
        ev = event_like(row, provider)
        sid = str((ev or {}).get("source_id") or (row.get("id") if isinstance(row, dict) else "") or "").strip()
        if sid and sid not in out:
            out.append(sid)
    return out


async def detail_calls(client: httpx.AsyncClient, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {(r.get("provider"), r.get("command")): r for r in results}
    specs: list[CallSpec] = []
    odds_ids = ids_from(by.get(("odds_api_io", "events_account1"), {}), "odds_api_io")[:10]
    if odds_ids and env("ODDS_API_IO_KEY"):
        specs.append(CallSpec("odds_api_io", "odds_multi_account1", "https://api.odds-api.io/v3/odds/multi", "odds_detail", {"apiKey": env("ODDS_API_IO_KEY"), "eventIds": ",".join(odds_ids), "bookmakers": "Bet365,Unibet"}))
    if odds_ids and (env("ODDS_API_IO_KEY_2") or env("ODDS_API_IO_KEY2")):
        specs.append(CallSpec("odds_api_io", "odds_multi_account2", "https://api.odds-api.io/v3/odds/multi", "odds_detail", {"apiKey": env("ODDS_API_IO_KEY_2") or env("ODDS_API_IO_KEY2"), "eventIds": ",".join(odds_ids), "bookmakers": "Betfair Exchange,Sbobet"}))
    bzz_row = by.get(("bzzoiro", "v2_events")) or by.get(("bzzoiro", "v1_events")) or by.get(("bzzoiro", "v1_predictions"))
    bzz_id = (ids_from(bzz_row or {}, "bzzoiro") or [""])[0]
    if bzz_id and env("BZZOIRO_API_KEY"):
        h = {"Authorization": f"Token {env('BZZOIRO_API_KEY')}"}
        for suffix, role in [("", "fixture_detail"), ("stats/", "xg_stats"), ("odds/", "odds_detail"), ("metadata/", "metadata"), ("lineups/", "lineups"), ("prediction/", "prediction")]:
            specs.append(CallSpec("bzzoiro", f"v2_event_{suffix.strip('/').replace('/', '_') or 'detail'}", f"https://sports.bzzoiro.com/api/v2/events/{bzz_id}/{suffix}", role, {}, h))
    sl_row = by.get(("sportlogic", "games_date_from_to")) or by.get(("sportlogic", "games_status_scheduled"))
    sl_id = (ids_from(sl_row or {}, "sportlogic") or [""])[0]
    sl_key = env("SPORTLOGIC_API_KEY") or env("SPORTLOGIC_KEY") or env("SPORTLOGIC_TOKEN")
    if sl_id and sl_key:
        root = env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
        h = {(env("SPORTLOGIC_HEADER_NAME", "X-API-Key") or "X-API-Key"): sl_key}
        specs += [CallSpec("sportlogic", "game_detail", f"{root}/games/{sl_id}", "fixture_detail", {}, h), CallSpec("sportlogic", "game_odds", f"{root}/games/{sl_id}/odds", "odds_detail", {}, h), CallSpec("sportlogic", "game_outcomes", f"{root}/outcomes/{sl_id}", "settlement", {}, h)]
    sem = asyncio.Semaphore(max(1, as_int(os.getenv("API_FULL_SMOKE_DETAIL_CONCURRENCY"), 3)))
    async def guarded(s: CallSpec) -> dict[str, Any]:
        async with sem:
            return await call(client, s)
    return await asyncio.gather(*(guarded(s) for s in specs)) if specs else []


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(str(r.get("status") or "unknown") for r in results)
    by_provider: dict[str, dict[str, int]] = defaultdict(dict)
    matchable: Counter[str] = Counter()
    for r in results:
        p = str(r.get("provider") or "unknown"); st = str(r.get("status") or "unknown")
        by_provider[p][st] = by_provider[p].get(st, 0) + 1
        if isinstance(r.get("quality"), dict) and r["quality"].get("usable_for_matching"):
            matchable[p] += 1
    return {"total_commands": len(results), "by_status": dict(by_status), "by_provider": dict(by_provider), "matchable_commands_by_provider": dict(matchable), "ok_commands": by_status.get("OK", 0), "empty_commands": by_status.get("EMPTY", 0), "rate_limited": by_status.get("RATE_LIMIT", 0), "auth_errors": by_status.get("AUTH", 0), "errors": by_status.get("ERROR", 0) + by_status.get("HTTP_ERROR", 0) + by_status.get("TIMEOUT", 0)}


def render(payload: dict[str, Any]) -> str:
    s = payload.get("summary", {})
    lines = ["🧩 API full-data enrichment diagnostics v7", f"• UTC: {payload.get('created_at_utc')}", f"• commands: {s.get('ok_commands', 0)} OK / {s.get('total_commands', 0)} total | empty {s.get('empty_commands', 0)} | 429 {s.get('rate_limited', 0)} | auth {s.get('auth_errors', 0)} | errors {s.get('errors', 0)}", "", "📊 By provider"]
    for p, counts in sorted((s.get("by_provider") or {}).items()):
        lines.append(f"• {p}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + f" | matchable={(s.get('matchable_commands_by_provider') or {}).get(p, 0)}")
    lines += ["", "📡 Commands"]
    for r in payload.get("results", []):
        q = r.get("quality") if isinstance(r.get("quality"), dict) else {}
        lines.append(f"• [{r.get('provider')}] {r.get('command')} ({r.get('role')}): {r.get('status')} http={r.get('http_status')} rows={r.get('rows_count')} matchable={q.get('usable_for_matching')}")
    return "\n".join(lines) + "\n"


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or 22)
    max_seconds = float(os.getenv("API_FULL_SMOKE_MAX_SECONDS") or 180)
    conc = max(1, as_int(os.getenv("API_FULL_SMOKE_CONCURRENCY"), 5))
    sem = asyncio.Semaphore(conc)
    specs = build_calls()
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True) as client:
        async def guarded(s: CallSpec) -> dict[str, Any]:
            async with sem:
                return await call(client, s)
        try:
            base_results = await asyncio.wait_for(asyncio.gather(*(guarded(s) for s in specs)), timeout=max_seconds)
        except asyncio.TimeoutError:
            base_results = []
        try:
            details = await asyncio.wait_for(detail_calls(client, base_results), timeout=max(20.0, max_seconds - (time.perf_counter() - started)))
        except Exception as exc:
            details = [{"provider": "dynamic_details", "command": "detail_calls", "role": "detail_probe", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}]
    results = base_results + details
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "updated_at_utc": datetime.now(UTC).isoformat(), "mode": "api_full_data_smoke_probe_v7", "status": "ok" if results else "no_results", "duration_seconds": round(time.perf_counter() - started, 2), "summary": summary(results), "results": results}
    JSON_OUT.write_text(json.dumps(safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
