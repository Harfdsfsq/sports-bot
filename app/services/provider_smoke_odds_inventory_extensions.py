from __future__ import annotations

"""Provider-smoke odds inventory extensions.

Adds quota-safe diagnostics for:
- secondary odds-api.io account inventory;
- Bzzoiro events that have odds rows;
- SportLogic odds endpoints;
- SStats possible odds endpoints.

This module is diagnostic-first. It does not mark SStats as a price source unless
its API actually returns odds-shaped rows.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
ODDS_EXT_JSON = OUT_DIR / "latest-provider-smoke-odds-extensions.json"
ODDS_EXT_TXT = OUT_DIR / "latest-provider-smoke-odds-extensions.txt"


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
        return [_sanitize(item) for item in value[:30]]
    if isinstance(value, str):
        return value[:1200]
    return value


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "result", "response", "fixtures", "matches", "events", "items", "games", "odds"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return ",".join(sorted(str(k) for k in value.keys())[:12])
    return type(value).__name__


def _has_odds_shape(rows: list[dict[str, Any]]) -> bool:
    price_keys = {"price", "odds", "odd", "decimal", "decimal_odds", "value", "home_odds", "away_odds", "draw_odds", "odd_1", "odd_x", "odd_2"}
    market_keys = {"market", "market_name", "market_key", "bookmaker", "bookmaker_name", "sportsbook", "outcomes", "selections", "options"}
    for row in rows:
        keys = {str(k).lower() for k in row.keys()}
        if keys & price_keys:
            return True
        if (keys & market_keys) and any(isinstance(row.get(k), (list, dict)) for k in row.keys()):
            return True
    return False


async def _get(client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[Any | None, dict[str, Any]]:
    started = datetime.now(UTC)
    try:
        response = await client.get(url, params=params or None, headers=headers or None)
    except Exception as exc:
        return None, {"ok": False, "status": "request_error", "error": f"{type(exc).__name__}: {exc}", "url": url, "params_keys": sorted((params or {}).keys())}
    try:
        payload = response.json()
    except Exception:
        payload = {"text_preview": response.text[:1000]}
    return payload, {
        "ok": response.status_code == 200,
        "http_status": response.status_code,
        "url": url,
        "params_keys": sorted((params or {}).keys()),
        "payload_shape": _shape(payload),
        "rows_count": len(_rows(payload)),
        "odds_shape": _has_odds_shape(_rows(payload)),
        "body_preview": response.text[:250],
        "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000.0, 1),
    }


async def fetch_secondary_odds_inventory(client: httpx.AsyncClient) -> dict[str, Any]:
    from app.services import provider_smoke_matching_diagnostics as base

    key = _secret("ODDS_API_IO_KEY_2", "ODDS_API_IO_ACC2_KEY", "ODDS_API_IO_SECONDARY_KEY")
    if not key:
        return {"status": "missing_key", "raw_rows": 0, "parsed_events": 0, "events": [], "attempts": []}
    now = datetime.now(UTC)
    limit = _env_int("PROVIDER_SMOKE_SECONDARY_ODDS_LIMIT", 100, 20)
    pages = _env_int("PROVIDER_SMOKE_SECONDARY_ODDS_PAGES", 2, 1)
    attempts: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        params = {
            "apiKey": key,
            "sport": "football",
            "status": "pending,live",
            "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "to": (now + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "limit": limit,
            "page": page,
        }
        payload, attempt = await _get(client, "https://api.odds-api.io/v3/events", params=params)
        attempts.append(attempt)
        batch = _rows(payload)
        for row in batch:
            event_id = str(row.get("id") or "").strip()
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            raw_rows.append(row)
        if len(batch) < limit:
            break
    events = [event for row in raw_rows if (event := base._event_from_generic("odds_api_io_2", row)) is not None and event.start is not None]
    return {"status": "ok" if any(a.get("ok") for a in attempts) else "request_failed", "raw_rows": len(raw_rows), "parsed_events": len(events), "events": events, "attempts": attempts[:4]}


async def fetch_bzzoiro_odds_inventory(client: httpx.AsyncClient) -> dict[str, Any]:
    from app.services import provider_smoke_matching_diagnostics as base

    key = _secret("BZZOIRO_API_KEY", "BZZOIRO_TOKEN")
    if not key:
        return {"status": "missing_key", "raw_rows": 0, "parsed_events": 0, "events": [], "attempts": []}
    now = datetime.now(UTC)
    headers = {"Authorization": f"Token {key}"}
    base_url = str(os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api").rstrip("/")
    payload, attempt = await _get(client, f"{base_url}/predictions/", params={"date_from": now.date().isoformat(), "date_to": (now + timedelta(days=2)).date().isoformat(), "upcoming": "true", "tz": "UTC", "page": 1}, headers=headers)
    rows = _rows(payload)
    limit = _env_int("PROVIDER_SMOKE_BZZOIRO_ODDS_INVENTORY_EVENT_LIMIT", 12, 1)
    events = []
    attempts = [attempt]
    for row in rows[:limit]:
        event_dict = row.get("event") if isinstance(row.get("event"), dict) else row
        event_id = str(event_dict.get("id") or row.get("id") or "").strip()
        if not event_id:
            continue
        odds_payload, odds_attempt = await _get(client, f"{base_url}/odds/", params={"event": event_id}, headers=headers)
        attempts.append(odds_attempt)
        odds_rows = _rows(odds_payload)
        if not odds_rows:
            continue
        event = base._event_from_generic("bzzoiro", row)
        if event and event.start is not None:
            event.provider = "bzzoiro_odds"
            events.append(event)
    return {"status": "ok" if attempt.get("ok") else "request_failed", "raw_rows": len(rows), "parsed_events": len(events), "events": events, "attempts": attempts[:8]}


async def probe_sportlogic_odds(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
    if not key:
        return {"status": "missing_key", "attempts": []}
    root = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    headers = {str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key"): key}
    games_payload, games_attempt = await _get(client, f"{root}/games", params={"per_page": 5}, headers=headers)
    games = _rows(games_payload)
    attempts = [games_attempt]
    odds_samples: list[dict[str, Any]] = []
    for row in games[: _env_int("PROVIDER_SMOKE_SPORTLOGIC_ODDS_GAME_LIMIT", 3, 1)]:
        game_id = str(row.get("id") or row.get("game_id") or row.get("fixture_id") or "").strip()
        if not game_id:
            continue
        for path, params in ((f"/games/{game_id}/odds", {}), ("/odds", {"game_id": game_id}), (f"/odds/{game_id}", {})):
            payload, attempt = await _get(client, f"{root}{path}", params=params, headers=headers)
            attempts.append(attempt)
            odds_rows = _rows(payload)
            if odds_rows:
                odds_samples.append({"game_id": game_id, "path": path, "rows_count": len(odds_rows), "odds_shape": _has_odds_shape(odds_rows), "sample": _sanitize(odds_rows[:3])})
                break
    return {"status": "ok" if games_attempt.get("ok") else "request_failed", "games_rows": len(games), "odds_payloads": len(odds_samples), "odds_samples": odds_samples, "attempts": attempts[:12]}


async def probe_sstats_odds(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("SSTATS_API_KEY")
    if not key:
        return {"status": "missing_key", "attempts": []}
    root = str(os.getenv("SSTATS_BASE_URL") or "https://api.sstats.net").rstrip("/")
    now = datetime.now(UTC)
    from_date = now.date().isoformat()
    to_date = (now + timedelta(days=1)).date().isoformat()
    variants = [
        ("/Games/list", {"from": from_date, "to": to_date, "limit": 20, "offset": 0, "apikey": key}),
        ("/Odds/list", {"from": from_date, "to": to_date, "limit": 20, "offset": 0, "apikey": key}),
        ("/Odds", {"from": from_date, "to": to_date, "limit": 20, "apikey": key}),
        ("/Games/odds", {"from": from_date, "to": to_date, "limit": 20, "apikey": key}),
    ]
    attempts: list[dict[str, Any]] = []
    odds_payloads: list[dict[str, Any]] = []
    for path, params in variants[: _env_int("PROVIDER_SMOKE_SSTATS_ODDS_VARIANTS", 4, 1)]:
        payload, attempt = await _get(client, f"{root}{path}", params=params)
        attempts.append(attempt)
        rows = _rows(payload)
        if rows and _has_odds_shape(rows):
            odds_payloads.append({"path": path, "rows_count": len(rows), "sample": _sanitize(rows[:3])})
    return {"status": "ok" if any(a.get("ok") for a in attempts) else "request_failed", "odds_payloads": len(odds_payloads), "odds_samples": odds_payloads, "attempts": attempts}


async def run_odds_extension_probe() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = max(18.0, float(os.getenv("PROVIDER_SMOKE_ODDS_EXTENSION_TIMEOUT") or 18.0))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), follow_redirects=True) as client:
        secondary, bzz, sportlogic, sstats = await asyncio.gather(
            fetch_secondary_odds_inventory(client),
            fetch_bzzoiro_odds_inventory(client),
            probe_sportlogic_odds(client),
            probe_sstats_odds(client),
            return_exceptions=True,
        )
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "secondary_odds_api_io": _pack_result(secondary),
        "bzzoiro_odds_inventory": _pack_result(bzz),
        "sportlogic_odds_probe": _pack_result(sportlogic),
        "sstats_odds_probe": _pack_result(sstats),
    }
    ODDS_EXT_JSON.write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ODDS_EXT_TXT.write_text(render_odds_extension_text(payload), encoding="utf-8")
    return payload


def _pack_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Exception):
        return {"status": "failed", "error": f"{type(value).__name__}: {value}"}
    if isinstance(value, dict):
        out = dict(value)
        events = out.get("events")
        if isinstance(events, list):
            out["sample_events"] = [e.sample() for e in events[:6] if hasattr(e, "sample")]
            out["events"] = []
        return out
    return {"status": "unknown"}


def render_odds_extension_text(payload: dict[str, Any]) -> str:
    lines = ["🎯 Extended odds inventory / odds probes", f"• UTC: {payload.get('created_at_utc')}", "", "| source | status | rows/games | parsed/events | odds payloads |", "| --- | --- | ---: | ---: | ---: |"]
    sec = payload.get("secondary_odds_api_io") or {}
    bzz = payload.get("bzzoiro_odds_inventory") or {}
    sl = payload.get("sportlogic_odds_probe") or {}
    ss = payload.get("sstats_odds_probe") or {}
    lines.append(f"| odds_api_io_2 | {sec.get('status')} | {sec.get('raw_rows', 0)} | {sec.get('parsed_events', 0)} | - |")
    lines.append(f"| bzzoiro_odds | {bzz.get('status')} | {bzz.get('raw_rows', 0)} | {bzz.get('parsed_events', 0)} | - |")
    lines.append(f"| sportlogic_odds | {sl.get('status')} | {sl.get('games_rows', 0)} | - | {sl.get('odds_payloads', 0)} |")
    lines.append(f"| sstats_odds | {ss.get('status')} | - | - | {ss.get('odds_payloads', 0)} |")
    lines.append("")
    lines.append("🔎 SportLogic odds attempts")
    for item in (sl.get("attempts") or [])[:8]:
        if isinstance(item, dict):
            lines.append(f"• http={item.get('http_status')} shape={item.get('payload_shape')} rows={item.get('rows_count')} odds_shape={item.get('odds_shape')} url={item.get('url')}")
    lines.append("")
    lines.append("🔎 SStats odds attempts")
    for item in (ss.get("attempts") or [])[:8]:
        if isinstance(item, dict):
            lines.append(f"• http={item.get('http_status')} shape={item.get('payload_shape')} rows={item.get('rows_count')} odds_shape={item.get('odds_shape')} url={item.get('url')}")
    return "\n".join(lines) + "\n"


async def build_unified_inventory(client: httpx.AsyncClient, primary_payload: dict[str, Any]) -> dict[str, Any]:
    from app.services import provider_smoke_matching_diagnostics as base

    merged = dict(primary_payload)
    events = list(primary_payload.get("events") or [])
    secondary = await fetch_secondary_odds_inventory(client)
    bzz = await fetch_bzzoiro_odds_inventory(client)
    for payload in (secondary, bzz):
        for event in payload.get("events") or []:
            events.append(event)
    merged["events"] = events
    merged["parsed_events"] = len(events)
    merged["raw_rows"] = int(primary_payload.get("raw_rows") or 0) + int(secondary.get("raw_rows") or 0) + int(bzz.get("raw_rows") or 0)
    merged["unified_sources"] = {
        "odds_api_io_primary_events": int(primary_payload.get("parsed_events") or 0),
        "odds_api_io_2_events": int(secondary.get("parsed_events") or 0),
        "bzzoiro_odds_events": int(bzz.get("parsed_events") or 0),
    }
    merged["samples"] = [event.sample() for event in events[:12] if hasattr(event, "sample")]
    return merged
