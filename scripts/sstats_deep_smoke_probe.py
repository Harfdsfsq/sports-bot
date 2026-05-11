from __future__ import annotations

"""Quota-safe deep SStats smoke probe.

The broad provider smoke currently uses SStats mostly as /Games/list historical
team-form. The uploaded OpenAPI spec shows much richer endpoints: game detail,
Glicko/xG probabilities, last-games-stats, text-summary, profits, injuries,
full prematch odds, live odds markers, LS mapping endpoints, teams/leagues and
market dictionaries. This probe tests those commands cheaply so the next
provider-smoke artifact tells us which SStats layers can be wired into the
coverage pipeline.
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
BASE_URL = "https://api.sstats.net"
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-deep-smoke.json"
TXT_OUT = OUT_DIR / "latest-sstats-deep-smoke.txt"
UA = "HARIZON-provider-smoke-sstats-deep/1.0"


@dataclass(frozen=True)
class CallSpec:
    command: str
    method: str
    path: str
    role: str
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None
    sample_detail: bool = False


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def today_msk() -> str:
    # Workflow runs with TZ=Europe/Moscow, but keep this deterministic.
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def tomorrow_msk() -> str:
    return ((datetime.now(UTC) + timedelta(hours=3)).date() + timedelta(days=1)).isoformat()


def yesterday_msk() -> str:
    return ((datetime.now(UTC) + timedelta(hours=3)).date() - timedelta(days=1)).isoformat()


def safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            if str(key).lower() in {"apikey", "api_key", "key", "token", "authorization"}:
                out[str(key)] = "***"
            else:
                out[str(key)] = safe(item, depth + 1)
        return out
    if isinstance(value, list):
        return [safe(item, depth + 1) for item in value[:8]]
    if isinstance(value, str):
        key = env("SSTATS_API_KEY")
        text = value.replace(key, "***") if key else value
        return text[:1200]
    return value


def csv_rows(text: str) -> list[Any]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        if "," in raw.splitlines()[0]:
            return list(csv.DictReader(io.StringIO(raw)))[:1000]
    except Exception:
        pass
    return raw.splitlines()[:100]


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, str):
        return csv_rows(payload)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "result", "results", "matches", "games", "odds", "leagues", "teams"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    # Do not count empty wrapper-only responses as rows.
    if set(str(k).lower() for k in payload.keys()).issubset({"status", "count", "message", "traceid", "requestquery", "offset", "totalcount"}):
        return []
    return [payload] if payload else []


def shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "list"
    if isinstance(payload, dict):
        return ",".join(sorted(str(k) for k in payload.keys())[:20])
    if isinstance(payload, str):
        return "text/csv" if "," in payload[:200] else "text"
    return type(payload).__name__


def extract_ids(rs: list[Any], limit: int) -> list[str]:
    ids: list[str] = []
    for row in rs:
        if not isinstance(row, dict):
            continue
        for key in ("id", "Id", "gameId", "GameId"):
            value = row.get(key)
            if value not in (None, ""):
                text = str(value).strip()
                if text and text not in ids:
                    ids.append(text)
                    break
        if len(ids) >= limit:
            break
    return ids


def event_like_count(rs: list[Any]) -> int:
    total = 0
    for row in rs[:200]:
        if not isinstance(row, dict):
            continue
        keys = {str(k).lower() for k in row.keys()}
        has_home = bool(keys & {"hometeam", "hometeamname", "home_team", "home"})
        has_away = bool(keys & {"awayteam", "awayteamname", "away_team", "away"})
        if has_home and has_away:
            total += 1
        elif isinstance(row.get("homeTeam"), dict) and isinstance(row.get("awayTeam"), dict):
            total += 1
    return total


def capability_flags(command: str, payload: Any, rs: list[Any]) -> list[str]:
    text_blob = json.dumps(safe(payload), ensure_ascii=False).lower()[:20000]
    flags: list[str] = []
    if event_like_count(rs):
        flags.append("fixture_matchable")
    if "odd" in text_blob or "winner1" in text_blob or "market" in text_blob or "bookmaker" in text_blob:
        flags.append("odds")
    if "xg" in text_blob or "expected" in text_blob:
        flags.append("xg")
    if "glicko" in command.lower() or "rating" in text_blob:
        flags.append("rating")
    if "lineup" in text_blob or "startxi" in text_blob:
        flags.append("lineups")
    if "injur" in command.lower() or "injur" in text_blob:
        flags.append("injuries")
    if "venue" in text_blob or "stadium" in text_blob or "referee" in text_blob:
        flags.append("venue_referee")
    if "profit" in command.lower() or "profit" in text_blob:
        flags.append("profits")
    if "summary" in command.lower() or command == "games_text_summary":
        flags.append("text_summary")
    return sorted(set(flags))


async def call(client: httpx.AsyncClient, spec: CallSpec, api_key: str) -> dict[str, Any]:
    params = dict(spec.params or {})
    if api_key and env("SSTATS_DEEP_SMOKE_INCLUDE_APIKEY", "true").lower() not in {"0", "false", "no"}:
        params.setdefault("apikey", api_key)
    started = time.perf_counter()
    payload: Any = None
    body = ""
    http_status: int | None = None
    error = ""
    try:
        if spec.method.upper() == "POST":
            response = await client.post(BASE_URL + spec.path, params=params or None, json=spec.json_body or {})
        else:
            response = await client.get(BASE_URL + spec.path, params=params or None)
        http_status = response.status_code
        body = response.text[:2000]
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
        status = "OK" if rs or isinstance(payload, (dict, str)) else "EMPTY"
    else:
        status = "HTTP_ERROR"
    return {
        "provider": "sstats",
        "command": spec.command,
        "method": spec.method,
        "path": spec.path,
        "role": spec.role,
        "status": status,
        "http_status": http_status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "rows_count": len(rs),
        "event_like_rows": event_like_count(rs),
        "payload_shape": shape(payload),
        "capabilities": capability_flags(spec.command, payload, rs),
        "sample": safe(rs[:5]),
        "body_preview": safe(body),
        "error": error,
        "params": safe(params),
    }


def base_calls() -> list[CallSpec]:
    t = today_msk()
    y = yesterday_msk()
    tm = tomorrow_msk()
    return [
        CallSpec("account_info", "GET", "/Account/Info", "auth_quota_identity", {}),
        CallSpec("leagues", "GET", "/Leagues", "league_mapping", {}),
        CallSpec("teams_search_arsenal", "GET", "/Teams/list", "team_mapping", {"Name": "Arsenal", "Limit": 20}),
        CallSpec("games_today_date", "GET", "/Games/list", "fixture_inventory", {"Date": t, "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1}),
        CallSpec("games_upcoming", "GET", "/Games/list", "fixture_inventory", {"Upcoming": "true", "TimeZone": 3, "Limit": 100, "Offset": 0, "Order": 1}),
        CallSpec("games_recent_lookback", "GET", "/Games/list", "historical_form_source", {"From": y, "To": tm, "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": -1}),
        CallSpec("ls_list_today", "GET", "/Ls/List", "flashscore_style_fixture_mapping", {"Date": t, "TimeZone": 3, "Limit": 100, "Offset": 0, "Order": 1}),
        CallSpec("ls_teams_arsenal", "GET", "/Ls/Teams", "ls_team_alias_mapping", {"name": "Arsenal"}),
        CallSpec("ls_leagues_premier", "GET", "/Ls/Leagues", "ls_league_alias_mapping", {"name": "Premier"}),
        CallSpec("odds_bookmakers", "GET", "/Odds/bookmakers", "bookmaker_mapping", {}),
        CallSpec("odds_prematch_markets", "GET", "/Odds/prematch-markets", "market_mapping", {}),
        CallSpec("odds_live_markets", "GET", "/Odds/live-markets", "live_market_mapping", {}),
        CallSpec("excel_delux_today", "GET", "/Excel/Delux", "all_in_one_excel_context", {"dayOffset": 0, "timeZone": 3}),
    ]


def detail_specs(game_id: str) -> list[CallSpec]:
    return [
        CallSpec("games_detail", "GET", f"/Games/{game_id}", "fixture_detail_stats_lineups_venue", {}, sample_detail=True),
        CallSpec("games_glicko", "GET", f"/Games/glicko/{game_id}", "glicko_rating_xg_probabilities", {}, sample_detail=True),
        CallSpec("games_last_games_stats", "GET", "/Games/last-games-stats", "prematch_team_form_xg_stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"}, sample_detail=True),
        CallSpec("games_text_summary", "GET", "/Games/text-summary", "textual_match_analysis", {"id": game_id, "limit": 25, "sameLeague": "true", "homeAway": "false"}, sample_detail=True),
        CallSpec("games_profits", "GET", "/Games/profits", "historical_bet_profitability", {"gameId": game_id, "limit": 25, "thisLeague": "false", "homeAway": "false", "sameGames": "false"}, sample_detail=True),
        CallSpec("games_injuries", "GET", "/Games/injuries", "injuries", {"gameId": game_id}, sample_detail=True),
        CallSpec("odds_prematch_full", "GET", f"/Odds/{game_id}", "full_prematch_odds", {"opening": "false"}, sample_detail=True),
    ]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(str(r.get("status") or "unknown") for r in results)
    by_role: dict[str, dict[str, int]] = defaultdict(dict)
    capabilities: Counter[str] = Counter()
    for row in results:
        role = str(row.get("role") or "unknown")
        status = str(row.get("status") or "unknown")
        by_role[role][status] = by_role[role].get(status, 0) + 1
        for cap in row.get("capabilities") or []:
            capabilities[str(cap)] += 1
    return {
        "total_commands": len(results),
        "ok_commands": by_status.get("OK", 0),
        "by_status": dict(by_status),
        "by_role": dict(by_role),
        "capability_hits": dict(capabilities),
        "total_rows": sum(int(r.get("rows_count") or 0) for r in results),
        "total_event_like_rows": sum(int(r.get("event_like_rows") or 0) for r in results),
    }


def render(payload: dict[str, Any]) -> str:
    s = payload.get("summary", {})
    lines = [
        "# SStats deep smoke probe",
        f"UTC: {payload.get('created_at_utc')}",
        f"Commands: {s.get('ok_commands', 0)} OK / {s.get('total_commands', 0)} total | rows={s.get('total_rows', 0)} | event_like={s.get('total_event_like_rows', 0)}",
        "",
        "## Capabilities detected",
    ]
    caps = s.get("capability_hits") or {}
    if caps:
        for cap, count in sorted(caps.items()):
            lines.append(f"- {cap}: {count}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Commands")
    for row in payload.get("results", []):
        caps_text = ",".join(row.get("capabilities") or []) or "-"
        lines.append(
            f"- {row.get('command')} [{row.get('role')}]: {row.get('status')} "
            f"http={row.get('http_status')} rows={row.get('rows_count')} event_like={row.get('event_like_rows')} caps={caps_text}"
        )
        if row.get("status") not in {"OK", "EMPTY"}:
            reason = row.get("error") or row.get("body_preview")
            if reason:
                lines.append(f"  reason: {str(reason)[:350]}")
    lines.append("")
    lines.append("## Integration meaning")
    lines.append("- /Games/list and /Ls/List are fixture/mapping sources.")
    lines.append("- /Games/{id}, /last-games-stats, /glicko, /profits, /injuries and /Odds/{id} should become per-match detail enrichment after SStats id matching.")
    lines.append("- /Odds/bookmakers and market dictionaries should be cached once per day.")
    return "\n".join(lines) + "\n"


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = env("SSTATS_API_KEY")
    max_detail_games = max(0, as_int(env("SSTATS_DEEP_SMOKE_DETAIL_GAMES"), 2))
    concurrency = max(1, as_int(env("SSTATS_DEEP_SMOKE_CONCURRENCY"), 3))
    timeout_seconds = float(env("SSTATS_DEEP_SMOKE_TIMEOUT_SECONDS", "18"))
    sem = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(timeout_seconds, connect=min(6.0, timeout_seconds))
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}) as client:
        async def guarded(spec: CallSpec) -> dict[str, Any]:
            async with sem:
                return await call(client, spec, api_key)
        base_specs = base_calls()
        base_results = await asyncio.gather(*(guarded(spec) for spec in base_specs))
        games_rows: list[Any] = []
        for result in base_results:
            if result.get("command") in {"games_today_date", "games_upcoming"}:
                games_rows.extend(result.get("sample") or [])
        ids = extract_ids(games_rows, max_detail_games)
        detail_results: list[dict[str, Any]] = []
        if ids:
            detail_all = []
            for gid in ids:
                detail_all.extend(detail_specs(gid))
            detail_results = await asyncio.gather(*(guarded(spec) for spec in detail_all))
    results = list(base_results) + list(detail_results)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "sstats_deep_smoke_probe_v1",
        "duration_seconds": round(time.perf_counter() - started, 2),
        "sample_game_ids": ids if 'ids' in locals() else [],
        "summary": summarize(results),
        "results": results,
        "notes": [
            "This probe is quota-safe and details only SSTATS_DEEP_SMOKE_DETAIL_GAMES sampled matches.",
            "Use detail endpoint success to wire SStats as form/xG/rating/lineups/injuries/odds provider, not only historical list provider.",
        ],
    }
    JSON_OUT.write_text(json.dumps(safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
