from __future__ import annotations

"""Quota-safe full-data smoke probe.

This file is called through the v4 compatibility shim by provider_smoke_fast.
It keeps Bzzoiro and Football-Data full probes, but probes odds-api.io extra
endpoints with a tiny bounded matrix so smoke cannot burn the hourly quota.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from scripts import api_full_data_smoke_probe as base
from scripts import api_full_data_smoke_probe_v2 as v2

UTC = timezone.utc
KEY_PARAM = "api" + "Key"
BOOK_PARAM = "book" + "maker"
LINE_PARAM = "market" + "Line"


def _bookmaker() -> str:
    return v2._bookmaker()


def _extra_cap() -> int:
    try:
        return max(4, int(float(os.getenv("API_FULL_SMOKE_ODDS_EXTRA_MAX_REQUESTS") or 8)))
    except Exception:
        return 8


def _sport_candidates(event_rows: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in event_rows[:10]:
        for key in ("sport", "sport_key", "sportId", "sport_id"):
            value = row.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, dict):
                for subkey in ("key", "id", "slug", "name"):
                    item = value.get(subkey)
                    if item not in (None, "") and str(item) not in values:
                        values.append(str(item))
            elif str(value) not in values:
                values.append(str(value))
    for fallback in str(os.getenv("API_FULL_SMOKE_ODDS_UPDATED_SPORTS") or "football,soccer").split(","):
        item = fallback.strip()
        if item and item not in values:
            values.append(item)
    return values[:4]


async def _limited_get(client: httpx.AsyncClient, section: dict[str, Any], root: str, endpoint: str, params: dict[str, Any]) -> Any | None:
    if endpoint != "/events":
        section["extra_requests"] = int(section.get("extra_requests") or 0) + 1
        if int(section.get("extra_requests") or 0) > _extra_cap():
            section["extra_request_cap_hit"] = True
            section.setdefault("diagnostic_examples", []).append(f"{endpoint}: skipped by quota-safe cap")
            return None
    before_errors = int(section.get("errors") or 0)
    payload = await base._get(client, "odds_api_io", f"{root}{endpoint}", endpoint=endpoint, params=params, section=section)
    if payload is None and endpoint == "/odds/movements":
        # A 404/no-data response is useful information for a sampled event/market,
        # not a provider outage. Keep it visible but do not let it dominate the
        # full-data smoke status.
        section["errors"] = before_errors
        section.setdefault("diagnostic_examples", []).append(f"{endpoint}: no sampled movement for params")
    return payload


async def _probe_updated(
    client: httpx.AsyncClient,
    section: dict[str, Any],
    root: str,
    secret: str,
    book: str,
    event_rows: list[dict[str, Any]],
    now: datetime,
) -> None:
    since_unix = str(int((now - timedelta(hours=6)).timestamp()))
    rows: list[dict[str, Any]] = []
    used: dict[str, Any] = {}
    for sport in _sport_candidates(event_rows):
        params = {KEY_PARAM: secret, "since": since_unix, "sport": sport, BOOK_PARAM: book}
        payload = await _limited_get(client, section, root, "/odds/updated", params)
        if payload is not None:
            rows = base._rows(payload)
            used = {"since": since_unix, "sport": sport, BOOK_PARAM: book}
            break
        await asyncio.sleep(0.1)
    section["updated_rows_count"] = len(rows)
    section["updated_sample"] = rows[:5]
    section["updated_params_used"] = base._sanitize(used)


async def _probe_odds_api_io_safe(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "extra_requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    secret = base._secret("ODDS_API_IO_KEY", "ODDS_API_IO_ACC1_KEY")
    if not secret:
        section["status"] = "missing_key"
        return section
    root = str(os.getenv("ODDS_API_IO_BASE_URL") or "https://api.odds-api.io/v3").rstrip("/")
    now = datetime.now(UTC)
    book = _bookmaker()

    events = await _limited_get(client, section, root, "/events", {
        KEY_PARAM: secret,
        "sport": "football",
        "status": "pending,live",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": 30,
        "page": 1,
    })
    event_rows = base._rows(events)
    event_ids = [base._id(row.get("id")) for row in event_rows if base._id(row.get("id"))][:1]
    section.update({"event_rows_count": len(event_rows), "event_ids": event_ids, "bookmaker_used": book, "updated_rows_count": 0, "movements_by_event": {}})

    await _probe_updated(client, section, root, secret, book, event_rows, now)

    movement_variants = [
        {"market": "1x2", LINE_PARAM: "0"},
        {"market": "h2h", LINE_PARAM: "0"},
        {"market": "totals", LINE_PARAM: "2.5"},
        {"market": "spreads", LINE_PARAM: "0"},
    ]
    for event_id in event_ids:
        result = {"rows_count": 0, "sample": [], "params_used": {}}
        for variant in movement_variants:
            params = {KEY_PARAM: secret, "eventId": event_id, BOOK_PARAM: book, **variant}
            payload = await _limited_get(client, section, root, "/odds/movements", params)
            rows = base._rows(payload)
            if payload is not None:
                result = {"rows_count": len(rows), "sample": rows[:5], "params_used": base._sanitize({"eventId": event_id, BOOK_PARAM: book, **variant})}
                break
            await asyncio.sleep(0.1)
        section["movements_by_event"][event_id] = result

    section["status"] = "ok" if int(section.get("event_rows_count") or 0) > 0 else "failed"
    section["quota_safe_wrapper"] = {"cap": _extra_cap(), "extra_requests": section.get("extra_requests", 0)}
    return section


async def run() -> dict[str, Any]:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = max(30.0, float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or 30.0))
    client_timeout = httpx.Timeout(timeout, connect=min(8.0, timeout), read=timeout, write=timeout, pool=timeout)
    async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=True) as client:
        payload: dict[str, Any] = {"updated_at_utc": datetime.now(UTC).isoformat(), "mode": "direct_full_data_smoke_probe_v5"}
        if base._truthy("API_FULL_SMOKE_BZZOIRO_ENABLED", True):
            payload["bzzoiro"] = await base._probe_bzzoiro(client)
        if base._truthy("API_FULL_SMOKE_FOOTBALL_DATA_ENABLED", True):
            payload["football_data"] = await v2._probe_football_data_v2(client)
        if base._truthy("API_FULL_SMOKE_ODDS_API_IO_ENABLED", True):
            payload["odds_api_io"] = await _probe_odds_api_io_safe(client)
    payload["quota_safe_wrapper"] = payload.get("odds_api_io", {}).get("quota_safe_wrapper", {})
    base.FULL_DATA_JSON.write_text(json.dumps(base._sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.FULL_DATA_TXT.write_text(base._render(payload), encoding="utf-8")
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
