from __future__ import annotations

"""Direct full-data probe for provider-smoke.

The runtime enrichment patch wraps real provider adapters, but provider-smoke is a
low-level HTTP diagnostic and does not always instantiate those adapters.  This
script directly probes the extra endpoints from the API documentation so every
smoke run shows which additional data is actually available.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
FULL_DATA_JSON = OUT_DIR / "latest-api-full-data-enrichment.json"
FULL_DATA_TXT = OUT_DIR / "latest-api-full-data-enrichment.txt"
RAW_CACHE_ROOT = Path(os.getenv("API_RAW_CACHE_DIR") or ".cache/api_raw")


def _secret(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(float(os.getenv(name) or default)))
    except Exception:
        return max(minimum, int(default))


def _truthy(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            low = str(key).lower()
            if any(token in low for token in ("key", "token", "secret", "authorization", "apikey", "api_key")):
                out[str(key)] = "***"
            else:
                out[str(key)] = _sanitize(item)
        return out
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:120]]
    if isinstance(value, str):
        return value[:1800]
    return value


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return ",".join(sorted(str(k) for k in value.keys())[:16])
    return type(value).__name__


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "data", "matches", "teams", "scorers", "events", "items", "response", "fixtures"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _dig(row: Any, *path: str) -> Any:
    cur = row
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _id(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _cache(api: str, endpoint: str, params: dict[str, Any], status: int | str, payload: Any, headers: dict[str, Any] | None = None) -> str:
    try:
        day = datetime.now(UTC).date().isoformat()
        digest = hashlib.sha256(json.dumps({"endpoint": endpoint, "params": _sanitize(params)}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:18]
        safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
        path = RAW_CACHE_ROOT / api / day / f"{safe_endpoint}-{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "api": api,
            "endpoint": endpoint,
            "params": _sanitize(params),
            "status": status,
            "headers": _sanitize(headers or {}),
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "payload_shape": _shape(payload),
            "rows_count": len(_rows(payload)),
            "raw_json": _sanitize(payload),
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path)
    except Exception:
        return ""


async def _get(client: httpx.AsyncClient, api: str, url: str, *, endpoint: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, section: dict[str, Any]) -> Any | None:
    section["requests"] = int(section.get("requests") or 0) + 1
    try:
        response = await client.get(url, params=params or None, headers=headers or None)
    except Exception as exc:
        section["errors"] = int(section.get("errors") or 0) + 1
        section.setdefault("error_examples", []).append(f"{endpoint}: {type(exc).__name__}: {exc}"[:500])
        return None
    try:
        payload = response.json()
    except Exception:
        payload = {"text_preview": response.text[:1800]}
    section.setdefault("http_statuses", []).append(response.status_code)
    section.setdefault("payload_shapes", []).append(_shape(payload))
    section.setdefault("raw_cache_files", []).append(_cache(api, endpoint, params or {}, response.status_code, payload, dict(response.headers)))
    if response.status_code != 200:
        section["errors"] = int(section.get("errors") or 0) + 1
        section.setdefault("error_examples", []).append(f"{endpoint}: http={response.status_code} body={response.text[:220]}"[:500])
        return None
    return payload


def _bzzoiro_event_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        event_id = _id(event.get("id") if isinstance(event, dict) else row.get("id"))
        if event_id and event_id not in ids:
            ids.append(event_id)
    return ids


def _bzzoiro_team_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        if not isinstance(event, dict):
            continue
        for key in ("home_team", "away_team", "home", "away", "team"):
            value = event.get(key)
            if isinstance(value, dict):
                team_id = _id(value.get("id") or value.get("team_id"))
                if team_id and team_id not in ids:
                    ids.append(team_id)
    return ids


def _bzzoiro_league_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        if not isinstance(event, dict):
            continue
        league_id = _id(_dig(event, "league", "id") or event.get("league_id"))
        if league_id and league_id not in ids:
            ids.append(league_id)
    return ids


async def _probe_bzzoiro(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    key = _secret("BZZOIRO_API_KEY", "BZZOIRO_TOKEN")
    if not key:
        section["status"] = "missing_key"
        return section
    base = str(os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api").rstrip("/")
    headers = {"Authorization": f"Token {key}"}
    now = datetime.now(UTC)
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    day_after = (now + timedelta(days=2)).date().isoformat()

    event_payload = await _get(client, "bzzoiro", f"{base}/events/", endpoint="/events/", headers=headers, params={"date_from": today, "date_to": day_after, "tz": "UTC", "limit": 100}, section=section)
    event_rows = _rows(event_payload)
    pred_payload = await _get(client, "bzzoiro", f"{base}/predictions/", endpoint="/predictions/", headers=headers, params={"date_from": today, "date_to": day_after, "upcoming": "true", "tz": "UTC", "limit": 100}, section=section)
    pred_rows = _rows(pred_payload)
    combined = event_rows + pred_rows
    event_ids = _bzzoiro_event_ids(combined)[: _env_int("API_FULL_SMOKE_BZZOIRO_EVENT_LIMIT", 4, 1)]
    team_ids = _bzzoiro_team_ids(combined)[: _env_int("API_FULL_SMOKE_BZZOIRO_TEAM_LIMIT", 4, 1)]
    league_ids = _bzzoiro_league_ids(combined)[: _env_int("API_FULL_SMOKE_BZZOIRO_LEAGUE_LIMIT", 3, 1)]

    live_payload = await _get(client, "bzzoiro", f"{base}/live/", endpoint="/live/", headers=headers, params={"tz": "UTC", "limit": 100}, section=section)
    live_rows = _rows(live_payload)
    section["live_rows_count"] = len(live_rows)
    section["live_sample"] = live_rows[:5]
    section["event_rows_count"] = len(event_rows)
    section["prediction_rows_count"] = len(pred_rows)
    section["event_ids"] = event_ids
    section["team_ids"] = team_ids
    section["league_ids"] = league_ids
    section["odds_by_event"] = {}
    section["social_by_event"] = {}
    section["standings_by_league"] = {}
    section["team_detail_by_id"] = {}
    section["squad_by_team"] = {}

    for event_id in event_ids:
        odds = await _get(client, "bzzoiro", f"{base}/odds/", endpoint="/odds/", headers=headers, params={"event": event_id}, section=section)
        odds_rows = _rows(odds)
        section["odds_by_event"][event_id] = {"rows_count": len(odds_rows), "sample": odds_rows[:5]}
        social = await _get(client, "bzzoiro", f"{base}/social/", endpoint="/social/", headers=headers, params={"event": event_id, "limit": 50}, section=section)
        social_rows = _rows(social)
        section["social_by_event"][event_id] = {"rows_count": len(social_rows), "sample": social_rows[:5]}
    for league_id in league_ids:
        standings = await _get(client, "bzzoiro", f"{base}/leagues/{league_id}/standings/", endpoint=f"/leagues/{league_id}/standings/", headers=headers, params={}, section=section)
        rows = _rows(standings)
        section["standings_by_league"][league_id] = {"rows_count": len(rows), "sample": rows[:5]}
    for team_id in team_ids:
        detail = await _get(client, "bzzoiro", f"{base}/teams/{team_id}/", endpoint=f"/teams/{team_id}/", headers=headers, params={}, section=section)
        detail_rows = _rows(detail)
        section["team_detail_by_id"][team_id] = {"shape": _shape(detail), "rows_count": len(detail_rows), "sample": detail_rows[:3] if detail_rows else _sanitize(detail)}
        squad = await _get(client, "bzzoiro", f"{base}/teams/{team_id}/squad/", endpoint=f"/teams/{team_id}/squad/", headers=headers, params={}, section=section)
        squad_rows = _rows(squad)
        section["squad_by_team"][team_id] = {"rows_count": len(squad_rows), "sample": squad_rows[:5]}
    section["status"] = "ok" if section["requests"] and section["errors"] < section["requests"] else "failed"
    return section


async def _probe_football_data(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    key = _secret("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY", "FOOTBALL_DATA_TOKEN")
    if not key:
        section["status"] = "missing_key"
        return section
    base = str(os.getenv("FOOTBALL_DATA_BASE_URL") or "https://api.football-data.org/v4").rstrip("/")
    headers = {"X-Auth-Token": key}
    now = datetime.now(UTC)
    today = now.date().isoformat()
    day_after = (now + timedelta(days=2)).date().isoformat()
    matches = await _get(client, "football_data", f"{base}/matches", endpoint="/matches", headers=headers, params={"dateFrom": today, "dateTo": day_after, "status": "SCHEDULED,TIMED"}, section=section)
    match_rows = _rows(matches)
    refs: list[str] = []
    for row in match_rows:
        comp = row.get("competition") if isinstance(row, dict) else None
        if isinstance(comp, dict):
            ref = _id(comp.get("code") or comp.get("id")).upper()
            if ref and ref not in refs:
                refs.append(ref)
    refs = refs[: _env_int("API_FULL_SMOKE_FOOTBALL_DATA_COMPETITION_LIMIT", 3, 1)]
    section["matches_rows_count"] = len(match_rows)
    section["competition_refs"] = refs
    section["teams_by_competition"] = {}
    section["scorers_by_competition"] = {}
    for ref in refs:
        teams = await _get(client, "football_data", f"{base}/competitions/{ref}/teams", endpoint=f"/competitions/{ref}/teams", headers=headers, params={}, section=section)
        team_rows = _rows(teams)
        section["teams_by_competition"][ref] = {"rows_count": len(team_rows), "sample": team_rows[:5]}
        scorers = await _get(client, "football_data", f"{base}/competitions/{ref}/scorers", endpoint=f"/competitions/{ref}/scorers", headers=headers, params={"limit": 20}, section=section)
        scorer_rows = _rows(scorers)
        section["scorers_by_competition"][ref] = {"rows_count": len(scorer_rows), "sample": scorer_rows[:5]}
    section["status"] = "ok" if section["requests"] and section["errors"] < section["requests"] else "failed"
    return section


async def _probe_odds_api_io(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    key = _secret("ODDS_API_IO_KEY", "ODDS_API_IO_ACC1_KEY")
    if not key:
        section["status"] = "missing_key"
        return section
    base = str(os.getenv("ODDS_API_IO_BASE_URL") or "https://api.odds-api.io/v3").rstrip("/")
    now = datetime.now(UTC)
    events = await _get(client, "odds_api_io", f"{base}/events", endpoint="/events", params={
        "apiKey": key,
        "sport": "football",
        "status": "pending,live",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": 30,
        "page": 1,
    }, section=section)
    event_rows = _rows(events)
    event_ids = [_id(row.get("id")) for row in event_rows if _id(row.get("id"))][:_env_int("API_FULL_SMOKE_ODDS_EVENT_LIMIT", 3, 1)]
    section["event_rows_count"] = len(event_rows)
    section["event_ids"] = event_ids
    section["updated_rows_count"] = 0
    section["movements_by_event"] = {}
    books = os.getenv("ODDS_API_IO_ACC1_BOOKMAKERS") or os.getenv("ODDS_API_IO_BOOKMAKERS") or "Bet365,Unibet"
    if _truthy("API_FULL_SMOKE_ODDS_UPDATED_ENABLED", True):
        since = (now - timedelta(hours=6)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        updated = await _get(client, "odds_api_io", f"{base}/odds/updated", endpoint="/odds/updated", params={"apiKey": key, "since": since, "sport": "football", "bookmakers": books}, section=section)
        updated_rows = _rows(updated)
        section["updated_rows_count"] = len(updated_rows)
        section["updated_sample"] = updated_rows[:5]
    for event_id in event_ids:
        movements = await _get(client, "odds_api_io", f"{base}/odds/movements", endpoint="/odds/movements", params={"apiKey": key, "eventId": event_id, "bookmakers": books}, section=section)
        rows = _rows(movements)
        section["movements_by_event"][event_id] = {"rows_count": len(rows), "sample": rows[:5]}
    section["status"] = "ok" if section["requests"] and section["errors"] < section["requests"] else "failed"
    return section


def _render(payload: dict[str, Any]) -> str:
    lines = [
        "🧩 API full-data enrichment diagnostics",
        f"• updated_at_utc: {payload.get('updated_at_utc')}",
        "",
        "| api | status | requests | errors | cache files | key rows |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for name in ("bzzoiro", "football_data", "odds_api_io"):
        section = payload.get(name)
        if not isinstance(section, dict):
            continue
        if name == "bzzoiro":
            key_rows = f"events={section.get('event_rows_count', 0)}, predictions={section.get('prediction_rows_count', 0)}, live={section.get('live_rows_count', 0)}, odds_events={len(section.get('odds_by_event') or {})}, teams={len(section.get('team_ids') or [])}, leagues={len(section.get('league_ids') or [])}"
        elif name == "football_data":
            key_rows = f"matches={section.get('matches_rows_count', 0)}, competitions={len(section.get('competition_refs') or [])}, teams={len(section.get('teams_by_competition') or {})}, scorers={len(section.get('scorers_by_competition') or {})}"
        else:
            key_rows = f"events={section.get('event_rows_count', 0)}, updated={section.get('updated_rows_count', 0)}, movements={len(section.get('movements_by_event') or {})}"
        lines.append(f"| {name} | {section.get('status')} | {section.get('requests', 0)} | {section.get('errors', 0)} | {len(section.get('raw_cache_files') or [])} | {key_rows} |")
    lines.append("")
    lines.append("🔎 Error examples")
    for name in ("bzzoiro", "football_data", "odds_api_io"):
        section = payload.get(name)
        if not isinstance(section, dict):
            continue
        for error in (section.get("error_examples") or [])[:5]:
            lines.append(f"• {name}: {error}")
    lines.append("")
    lines.append("📁 Raw payload cache: .cache/api_raw/<api>/<date>/")
    return "\n".join(lines) + "\n"


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or 18.0)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), follow_redirects=True) as client:
        tasks = []
        if _truthy("API_FULL_SMOKE_BZZOIRO_ENABLED", True):
            tasks.append(("bzzoiro", _probe_bzzoiro(client)))
        if _truthy("API_FULL_SMOKE_FOOTBALL_DATA_ENABLED", True):
            tasks.append(("football_data", _probe_football_data(client)))
        if _truthy("API_FULL_SMOKE_ODDS_API_IO_ENABLED", True):
            tasks.append(("odds_api_io", _probe_odds_api_io(client)))
        results = await asyncio.gather(*(item[1] for item in tasks), return_exceptions=True)
    payload: dict[str, Any] = {"updated_at_utc": datetime.now(UTC).isoformat(), "mode": "direct_full_data_smoke_probe"}
    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            payload[name] = {"status": "failed", "requests": 0, "errors": 1, "error_examples": [f"{type(result).__name__}: {result}"]}
        else:
            payload[name] = result
    FULL_DATA_JSON.write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FULL_DATA_TXT.write_text(_render(payload), encoding="utf-8")
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(_render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
