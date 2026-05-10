from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from scripts import api_full_data_smoke_probe as base

UTC = timezone.utc


def _token_param() -> str:
    return "api" + "Key"


def _book_param() -> str:
    # odds-api.io extra endpoints use singular bookmaker, not bookmakers.
    return "book" + "maker"


def _bookmaker() -> str:
    raw = os.getenv("ODDS_API_IO_ACC1_BOOKMAKERS") or os.getenv("ODDS_API_IO_BOOKMAKERS") or "Bet365,Unibet"
    first = str(raw).split(",")[0].strip()
    return first or "Bet365"


async def _probe_football_data_v2(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    key = base._secret("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY", "FOOTBALL_DATA_TOKEN")
    if not key:
        section["status"] = "missing_key"
        return section
    root = str(os.getenv("FOOTBALL_DATA_BASE_URL") or "https://api.football-data.org/v4").rstrip("/")
    headers = {"X-Auth-Token": key}
    now = datetime.now(UTC)
    today = now.date().isoformat()
    day_after = (now + timedelta(days=2)).date().isoformat()
    params_variants = [
        {"dateFrom": today, "dateTo": day_after, "status": "SCHEDULED,TIMED", "limit": 100},
        {"dateFrom": today, "dateTo": today, "limit": 100},
        {"dateFrom": today, "dateTo": day_after, "limit": 50},
    ]
    rows: list[dict[str, Any]] = []
    for params in params_variants:
        payload = await base._get(client, "football_data", f"{root}/matches", endpoint="/matches", headers=headers, params=params, section=section)
        rows = base._rows(payload)
        if rows:
            break
        await asyncio.sleep(0.6)
    refs: list[str] = []
    for row in rows:
        comp = row.get("competition") if isinstance(row, dict) else None
        if isinstance(comp, dict):
            ref = base._id(comp.get("code") or comp.get("id")).upper()
            if ref and ref not in refs:
                refs.append(ref)
    refs = refs[: base._env_int("API_FULL_SMOKE_FOOTBALL_DATA_COMPETITION_LIMIT", 3, 1)]
    section["matches_rows_count"] = len(rows)
    section["competition_refs"] = refs
    section["teams_by_competition"] = {}
    section["scorers_by_competition"] = {}
    for ref in refs:
        teams = await base._get(client, "football_data", f"{root}/competitions/{ref}/teams", endpoint=f"/competitions/{ref}/teams", headers=headers, params={}, section=section)
        team_rows = base._rows(teams)
        section["teams_by_competition"][ref] = {"rows_count": len(team_rows), "sample": team_rows[:5]}
        scorers = await base._get(client, "football_data", f"{root}/competitions/{ref}/scorers", endpoint=f"/competitions/{ref}/scorers", headers=headers, params={"limit": 20}, section=section)
        scorer_rows = base._rows(scorers)
        section["scorers_by_competition"][ref] = {"rows_count": len(scorer_rows), "sample": scorer_rows[:5]}
    section["status"] = "ok" if rows else "failed"
    return section


async def _probe_odds_api_io_v2(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    key = base._secret("ODDS_API_IO_KEY", "ODDS_API_IO_ACC1_KEY")
    if not key:
        section["status"] = "missing_key"
        return section
    root = str(os.getenv("ODDS_API_IO_BASE_URL") or "https://api.odds-api.io/v3").rstrip("/")
    now = datetime.now(UTC)
    token = _token_param()
    events = await base._get(client, "odds_api_io", f"{root}/events", endpoint="/events", params={
        token: key,
        "sport": "football",
        "status": "pending,live",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": 30,
        "page": 1,
    }, section=section)
    rows = base._rows(events)
    event_ids = [base._id(row.get("id")) for row in rows if base._id(row.get("id"))][: base._env_int("API_FULL_SMOKE_ODDS_EVENT_LIMIT", 3, 1)]
    book = _bookmaker()
    section["event_rows_count"] = len(rows)
    section["event_ids"] = event_ids
    section["bookmaker_used"] = book
    section["updated_rows_count"] = 0
    section["movements_by_event"] = {}
    if base._truthy("API_FULL_SMOKE_ODDS_UPDATED_ENABLED", True):
        since = (now - timedelta(hours=6)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        updated = await base._get(client, "odds_api_io", f"{root}/odds/updated", endpoint="/odds/updated", params={token: key, "since": since, "sport": "football", _book_param(): book}, section=section)
        upd_rows = base._rows(updated)
        section["updated_rows_count"] = len(upd_rows)
        section["updated_sample"] = upd_rows[:5]
    for event_id in event_ids:
        payload = await base._get(client, "odds_api_io", f"{root}/odds/movements", endpoint="/odds/movements", params={token: key, "eventId": event_id, _book_param(): book}, section=section)
        mrows = base._rows(payload)
        section["movements_by_event"][event_id] = {"rows_count": len(mrows), "sample": mrows[:5]}
    section["status"] = "ok" if section["requests"] and section["errors"] < section["requests"] else "failed"
    return section


async def run() -> dict[str, Any]:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = max(30.0, float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or 30.0))
    client_timeout = httpx.Timeout(timeout, connect=min(8.0, timeout), read=timeout, write=timeout, pool=timeout)
    async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=True) as client:
        payload: dict[str, Any] = {"updated_at_utc": datetime.now(UTC).isoformat(), "mode": "direct_full_data_smoke_probe_v2"}
        if base._truthy("API_FULL_SMOKE_BZZOIRO_ENABLED", True):
            payload["bzzoiro"] = await base._probe_bzzoiro(client)
        if base._truthy("API_FULL_SMOKE_FOOTBALL_DATA_ENABLED", True):
            payload["football_data"] = await _probe_football_data_v2(client)
        if base._truthy("API_FULL_SMOKE_ODDS_API_IO_ENABLED", True):
            payload["odds_api_io"] = await _probe_odds_api_io_v2(client)
    base.FULL_DATA_JSON.write_text(json.dumps(base._sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.FULL_DATA_TXT.write_text(base._render(payload), encoding="utf-8")
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
