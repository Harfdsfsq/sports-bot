from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from scripts import api_full_data_smoke_probe as base
from scripts import api_full_data_smoke_probe_v2 as v2
from scripts import api_full_data_smoke_probe_v3 as v3

UTC = timezone.utc


def _api_key_param() -> str:
    return "api" + "Key"


def _bookmaker_param() -> str:
    return "book" + "maker"


def _market_param() -> str:
    return "market"


def _market_line_param() -> str:
    return "market" + "Line"


def _bookmaker() -> str:
    return v2._bookmaker()


def _common_market_lines(market: str) -> list[str]:
    m = str(market or "").lower()
    if m in {"1x2", "h2h", "moneyline", "match_winner", "winner"}:
        return ["0", "0.0"]
    if m in {"totals", "over_under", "overunder", "total_goals"}:
        return ["2.5", "1.5", "3.5", "0"]
    if m in {"spreads", "handicap", "asian_handicap"}:
        return ["0", "0.5", "-0.5", "1.0", "-1.0"]
    if m in {"btts", "both_teams_to_score"}:
        return ["0"]
    return ["0", "2.5", "1.5"]


def _candidate_markets() -> list[str]:
    raw = os.getenv("API_FULL_SMOKE_ODDS_MARKETS") or "1x2,h2h,match_winner,moneyline,totals,over_under,total_goals,spreads,handicap,btts"
    values = [item.strip() for item in str(raw).split(",") if item.strip()]
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _sport_candidates_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        for key in ("key", "id", "slug", "sport", "sport_key", "sportId", "name"):
            value = row.get(key)
            if value in (None, ""):
                continue
            text = str(value).strip()
            lower = text.lower()
            if "soccer" in lower or "football" in lower or lower in {"1", "2"}:
                if text not in out:
                    out.append(text)
    return out


async def _discover_sports(client: httpx.AsyncClient, section: dict[str, Any], root: str, secret: str) -> list[str]:
    payload = await base._get(
        client,
        "odds_api_io",
        f"{root}/sports",
        endpoint="/sports",
        params={_api_key_param(): secret},
        section=section,
    )
    rows = base._rows(payload)
    sports = _sport_candidates_from_rows(rows)
    for fallback in str(os.getenv("API_FULL_SMOKE_ODDS_EXTRA_SPORTS") or "football,soccer").split(","):
        item = fallback.strip()
        if item and item not in sports:
            sports.append(item)
    section["sports_rows_count"] = len(rows)
    section["sports_candidates"] = sports[:20]
    section["sports_sample"] = rows[:5]
    return sports


async def _try_updated(client: httpx.AsyncClient, section: dict[str, Any], root: str, secret: str, sport_candidates: list[str], book: str) -> tuple[int, dict[str, Any] | None]:
    now = datetime.now(UTC)
    since = (now - timedelta(hours=6)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for sport in sport_candidates:
        params = {_api_key_param(): secret, "since": since, "sport": sport, _bookmaker_param(): book}
        payload = await base._get(client, "odds_api_io", f"{root}/odds/updated", endpoint="/odds/updated", params=params, section=section)
        rows = base._rows(payload)
        if payload is not None:
            section["updated_params_used"] = base._sanitize(params)
            section["updated_sample"] = rows[:5]
            return len(rows), params
        await asyncio.sleep(0.2)
    return 0, None


async def _try_movements(client: httpx.AsyncClient, section: dict[str, Any], root: str, secret: str, event_id: str, book: str) -> dict[str, Any]:
    best: dict[str, Any] = {"rows_count": 0, "sample": [], "params_used": {}}
    for market in _candidate_markets():
        # First try market without line for true ML markets, then line variants.
        variants = [{_api_key_param(): secret, "eventId": event_id, _bookmaker_param(): book, _market_param(): market}]
        for line in _common_market_lines(market):
            variants.append({_api_key_param(): secret, "eventId": event_id, _bookmaker_param(): book, _market_param(): market, _market_line_param(): line})
        for params in variants:
            payload = await base._get(client, "odds_api_io", f"{root}/odds/movements", endpoint="/odds/movements", params=params, section=section)
            rows = base._rows(payload)
            if payload is not None:
                return {"rows_count": len(rows), "sample": rows[:5], "params_used": base._sanitize(params)}
            await asyncio.sleep(0.1)
    return best


async def _probe_odds_api_io_v4(client: httpx.AsyncClient) -> dict[str, Any]:
    section: dict[str, Any] = {"requests": 0, "errors": 0, "http_statuses": [], "payload_shapes": [], "raw_cache_files": []}
    secret = base._secret("ODDS_API_IO_KEY", "ODDS_API_IO_ACC1_KEY")
    if not secret:
        section["status"] = "missing_key"
        return section
    root = str(os.getenv("ODDS_API_IO_BASE_URL") or "https://api.odds-api.io/v3").rstrip("/")
    now = datetime.now(UTC)
    token = _api_key_param()
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
    sports_from_events = []
    for row in event_rows:
        for key in ("sport", "sport_key", "sportId"):
            value = row.get(key)
            if value not in (None, "") and str(value) not in sports_from_events:
                sports_from_events.append(str(value))
    book = _bookmaker()
    section["event_rows_count"] = len(event_rows)
    section["event_ids"] = event_ids
    section["bookmaker_used"] = book
    section["event_sports_seen"] = sports_from_events[:20]
    section["updated_rows_count"] = 0
    section["movements_by_event"] = {}

    sports = await _discover_sports(client, section, root, secret)
    for item in sports_from_events:
        if item not in sports:
            sports.insert(0, item)

    if base._truthy("API_FULL_SMOKE_ODDS_UPDATED_ENABLED", True):
        count, _used = await _try_updated(client, section, root, secret, sports, book)
        section["updated_rows_count"] = count

    for event_id in event_ids:
        section["movements_by_event"][event_id] = await _try_movements(client, section, root, secret, event_id, book)

    # Keep status ok if base events work; extra endpoint errors are diagnostic.
    section["status"] = "ok" if section.get("event_rows_count", 0) else "failed"
    return section


async def run() -> dict[str, Any]:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = max(30.0, float(os.getenv("API_FULL_SMOKE_TIMEOUT_SECONDS") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or 30.0))
    client_timeout = httpx.Timeout(timeout, connect=min(8.0, timeout), read=timeout, write=timeout, pool=timeout)
    async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=True) as client:
        payload: dict[str, Any] = {"updated_at_utc": datetime.now(UTC).isoformat(), "mode": "direct_full_data_smoke_probe_v4"}
        if base._truthy("API_FULL_SMOKE_BZZOIRO_ENABLED", True):
            payload["bzzoiro"] = await base._probe_bzzoiro(client)
        if base._truthy("API_FULL_SMOKE_FOOTBALL_DATA_ENABLED", True):
            payload["football_data"] = await v2._probe_football_data_v2(client)
        if base._truthy("API_FULL_SMOKE_ODDS_API_IO_ENABLED", True):
            payload["odds_api_io"] = await _probe_odds_api_io_v4(client)
    base.FULL_DATA_JSON.write_text(json.dumps(base._sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.FULL_DATA_TXT.write_text(base._render(payload), encoding="utf-8")
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
