from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from scripts import api_full_data_smoke_probe as base
from scripts import api_full_data_smoke_probe_v2 as v2

UTC = timezone.utc


def _token_key() -> str:
    return "api" + "Key"


def _book_key() -> str:
    return "book" + "maker"


def _market_key() -> str:
    return "market"


def _markets() -> list[str]:
    raw = os.getenv("API_FULL_SMOKE_ODDS_MARKETS") or "1x2,match_winner,h2h,totals,over_under,spreads,btts"
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _sports() -> list[str]:
    raw = os.getenv("API_FULL_SMOKE_ODDS_EXTRA_SPORTS") or "soccer,football"
    values = [item.strip() for item in str(raw).split(",") if item.strip()]
    return values or ["soccer", "football"]


async def _first_ok(client: httpx.AsyncClient, section: dict[str, Any], api: str, url: str, endpoint: str, variants: list[dict[str, Any]]) -> tuple[Any | None, dict[str, Any] | None]:
    for params in variants:
        payload = await base._get(client, api, url, endpoint=endpoint, params=params, section=section)
        if payload is not None:
            return payload, params
        await asyncio.sleep(0.25)
    return None, None


async def _probe_odds_api_io_v3(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    secret = base._secret("ODDS_API_IO_KEY", "ODDS_API_IO_ACC1_KEY")
    if not secret:
        section["status"] = "missing_key"
        return section
    root = str(os.getenv("ODDS_API_IO_BASE_URL") or "https://api.odds-api.io/v3").rstrip("/")
    now = datetime.now(UTC)
    token = _token_key()
    events = await base._get(client, "odds_api_io", f"{root}/events", endpoint="/events", params={
        token: secret,
        "sport": "football",
        "status": "pending,live",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": 30,
        "page": 1,
    }, section=section)
    event_rows = base._rows(events)
    event_ids = [base._id(row.get("id")) for row in event_rows if base._id(row.get("id"))][: base._env_int("API_FULL_SMOKE_ODDS_EVENT_LIMIT", 3, 1)]
    book = v2._bookmaker()
    section["event_rows_count"] = len(event_rows)
    section["event_ids"] = event_ids
    section["bookmaker_used"] = book
    section["sports_tried"] = []
    section["markets_tried"] = []
    section["updated_rows_count"] = 0
    section["movements_by_event"] = {}

    if base._truthy("API_FULL_SMOKE_ODDS_UPDATED_ENABLED", True):
        since = (now - timedelta(hours=6)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        variants = []
        for sport in _sports():
            variants.append({token: secret, "since": since, "sport": sport, _book_key(): book})
        updated, used = await _first_ok(client, section, "odds_api_io", f"{root}/odds/updated", "/odds/updated", variants)
        if used:
            section["updated_params_used"] = base._sanitize(used)
        updated_rows = base._rows(updated)
        section["updated_rows_count"] = len(updated_rows)
        section["updated_sample"] = updated_rows[:5]

    for event_id in event_ids:
        variants = []
        for market in _markets():
            variants.append({token: secret, "eventId": event_id, _book_key(): book, _market_key(): market})
        payload, used = await _first_ok(client, section, "odds_api_io", f"{root}/odds/movements", "/odds/movements", variants)
        rows = base._rows(payload)
        section["movements_by_event"][event_id] = {
            "rows_count": len(rows),
            "sample": rows[:5],
            "params_used": base._sanitize(used or {}),
        }
        if used and used.get(_market_key()) not in section["markets_tried"]:
            section["markets_tried"].append(used.get(_market_key()))
    section["status"] = "ok" if section["requests"] and section["errors"] < section["requests"] else "failed"
    return section


async def run() -> dict[str, Any]:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = max(30.0, float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or 30.0))
    client_timeout = httpx.Timeout(timeout, connect=min(8.0, timeout), read=timeout, write=timeout, pool=timeout)
    async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=True) as client:
        payload: dict[str, Any] = {"updated_at_utc": datetime.now(UTC).isoformat(), "mode": "direct_full_data_smoke_probe_v3"}
        if base._truthy("API_FULL_SMOKE_BZZOIRO_ENABLED", True):
            payload["bzzoiro"] = await base._probe_bzzoiro(client)
        if base._truthy("API_FULL_SMOKE_FOOTBALL_DATA_ENABLED", True):
            payload["football_data"] = await v2._probe_football_data_v2(client)
        if base._truthy("API_FULL_SMOKE_ODDS_API_IO_ENABLED", True):
            payload["odds_api_io"] = await _probe_odds_api_io_v3(client)
    base.FULL_DATA_JSON.write_text(json.dumps(base._sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.FULL_DATA_TXT.write_text(base._render(payload), encoding="utf-8")
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
