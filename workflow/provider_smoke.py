from __future__ import annotations

"""Fast smoke check for the exact HARIZON core provider set.

Allowed providers only:
- odds_api_io
- sstats
- bzzoiro
- football_data
- thesportsdb
- WeatherAPI
- Open-Meteo
- ClubElo

The script is intentionally lightweight. It verifies auth presence and performs
at most one cheap request per provider, then writes:
- .data/exports/latest-provider-smoke.json
- .data/exports/latest-provider-smoke.md

It does not fail the workflow by default. Set PROVIDER_SMOKE_FAIL_ON_ERROR=true
if you want a hard gate later.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
ROOT = Path(".").resolve()
OUT_DIR = ROOT / ".data" / "exports"
JSON_PATH = OUT_DIR / "latest-provider-smoke.json"
MD_PATH = OUT_DIR / "latest-provider-smoke.md"
TIMEOUT = float(os.getenv("PROVIDER_SMOKE_TIMEOUT_SECONDS") or 10.0)

ALLOWED = [
    "odds_api_io",
    "sstats",
    "bzzoiro",
    "football_data",
    "thesportsdb",
    "weatherapi",
    "open_meteo",
    "clubelo",
]

NON_CORE_ENV_PREFIXES = [
    "ALLSPORTSAPI",
    "SPORTLOGIC",
    "ODDSPAPI",
    "ODDS_FEED",
    "RAPIDAPI_ODDS_FEED",
    "HIGHLIGHTLY",
    "API_FOOTBALL",
    "FUTRIXMETRICS",
    "NEWSAPI",
    "CURRENTS",
    "GNEWS",
    "NEWSDATA",
    "GUARDIAN",
    "METEOSTAT",
    "OPENWEATHERMAP",
    "RAPIDAPI_SPORTSBOOK",
    "RAPIDAPI_FREE_FOOTBALL",
    "SHARPAPI",
    "WIKIDATA",
    "OPENFOOTBALL",
    "BOOKIES_API",
]


def _secret(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "list"
    if isinstance(payload, dict):
        return ",".join(sorted(str(k) for k in payload.keys())[:10])
    return type(payload).__name__


def _count_items(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "results", "events", "matches", "response", "teams"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return len(payload)
    return 0


def _row(name: str, *, enabled: bool, ok: bool, status: str, **extra: Any) -> dict[str, Any]:
    return {"provider": name, "enabled": enabled, "ok": ok, "status": status, **extra}


async def check_odds_api_io(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("ODDS_API_IO_KEY")
    key2 = _secret("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    if not key:
        return _row("odds_api_io", enabled=False, ok=False, status="missing_key", key_present=False, key2_present=bool(key2))
    now = datetime.now(UTC)
    params = {
        "apiKey": key,
        "sport": "football",
        "status": "pending",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": 5,
        "page": 1,
    }
    try:
        response = await client.get("https://api.odds-api.io/v3/events", params=params)
    except Exception as exc:
        return _row("odds_api_io", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=True, key2_present=bool(key2))
    payload = _safe_json(response)
    ok = response.status_code == 200 and isinstance(payload, list)
    return _row(
        "odds_api_io",
        enabled=True,
        ok=ok,
        status="ok" if ok else f"http_{response.status_code}",
        http_status=response.status_code,
        key_present=True,
        key2_present=bool(key2),
        item_count=_count_items(payload),
        payload_shape=_shape(payload),
        body_preview=response.text[:300],
    )


async def check_sstats(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("SSTATS_API_KEY")
    if not key:
        return _row("sstats", enabled=False, ok=False, status="missing_key", key_present=False)
    to_date = datetime.now(UTC).date()
    from_date = to_date - timedelta(days=1)
    params = {"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": 5, "offset": 0, "apikey": key}
    try:
        response = await client.get("https://api.sstats.net/Games/list", params=params)
    except Exception as exc:
        return _row("sstats", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=True)
    payload = _safe_json(response)
    ok = response.status_code == 200 and payload is not None
    return _row("sstats", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=True, item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_bzzoiro(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("BZZOIRO_API_KEY")
    if not key:
        return _row("bzzoiro", enabled=False, ok=False, status="missing_key", key_present=False)
    today = datetime.now(UTC).date().isoformat()
    params = {"date_from": today, "date_to": today, "upcoming": "true", "tz": "UTC", "page": 1}
    headers = {"Authorization": f"Token {key}"}
    try:
        response = await client.get("https://sports.bzzoiro.com/api/predictions/", params=params, headers=headers)
    except Exception as exc:
        return _row("bzzoiro", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=True)
    payload = _safe_json(response)
    ok = response.status_code == 200 and payload is not None
    return _row("bzzoiro", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=True, item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_football_data(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY")
    if not key:
        return _row("football_data", enabled=False, ok=False, status="missing_key", key_present=False)
    today = datetime.now(UTC).date().isoformat()
    params = {"dateFrom": today, "dateTo": today}
    headers = {"X-Auth-Token": key}
    try:
        response = await client.get("https://api.football-data.org/v4/matches", params=params, headers=headers)
    except Exception as exc:
        return _row("football_data", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=True)
    payload = _safe_json(response)
    ok = response.status_code == 200 and payload is not None
    return _row("football_data", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=True, item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_thesportsdb(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("THESPORTSDB_API_KEY") or "123"
    try:
        response = await client.get(f"https://www.thesportsdb.com/api/v1/json/{key}/searchteams.php", params={"t": "Arsenal"})
    except Exception as exc:
        return _row("thesportsdb", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=bool(_secret("THESPORTSDB_API_KEY")), using_free_key=not bool(_secret("THESPORTSDB_API_KEY")))
    payload = _safe_json(response)
    ok = response.status_code == 200 and payload is not None
    return _row("thesportsdb", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=bool(_secret("THESPORTSDB_API_KEY")), using_free_key=not bool(_secret("THESPORTSDB_API_KEY")), item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_weatherapi(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY")
    if not key:
        return _row("weatherapi", enabled=False, ok=False, status="missing_key", key_present=False)
    try:
        response = await client.get("https://api.weatherapi.com/v1/current.json", params={"key": key, "q": "London", "aqi": "no"})
    except Exception as exc:
        return _row("weatherapi", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}", key_present=True)
    payload = _safe_json(response)
    ok = response.status_code == 200 and isinstance(payload, dict) and "current" in payload
    return _row("weatherapi", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=True, item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_open_meteo(client: httpx.AsyncClient) -> dict[str, Any]:
    params = {"latitude": 51.5072, "longitude": -0.1276, "current": "temperature_2m,precipitation,wind_speed_10m"}
    try:
        response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
    except Exception as exc:
        return _row("open_meteo", enabled=True, ok=False, status="request_error", error=f"{type(exc).__name__}: {exc}")
    payload = _safe_json(response)
    ok = response.status_code == 200 and isinstance(payload, dict) and "current" in payload
    return _row("open_meteo", enabled=True, ok=ok, status="ok" if ok else f"http_{response.status_code}", http_status=response.status_code, key_present=True, key_required=False, item_count=_count_items(payload), payload_shape=_shape(payload), body_preview=response.text[:300])


async def check_clubelo(client: httpx.AsyncClient) -> dict[str, Any]:
    urls = [
        "https://api.clubelo.com/Arsenal",
        "https://api.clubelo.com/2026-05-09",
    ]
    last_error = ""
    for url in urls:
        try:
            response = await client.get(url)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        ok = response.status_code == 200 and bool(response.text.strip())
        if ok:
            rows = max(0, len(response.text.splitlines()) - 1)
            return _row("clubelo", enabled=True, ok=True, status="ok", http_status=response.status_code, key_present=True, key_required=False, item_count=rows, payload_shape="csv", body_preview=response.text[:300])
        last_error = f"http_{response.status_code}"
    return _row("clubelo", enabled=True, ok=False, status=last_error or "failed", key_required=False)


def non_core_env_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prefix in NON_CORE_ENV_PREFIXES:
        matching = {k: v for k, v in os.environ.items() if k.startswith(prefix)}
        enabled_keys = {k: v for k, v in matching.items() if k.endswith("_ENABLED") or k.startswith("ENABLE_")}
        limit_keys = {k: v for k, v in matching.items() if any(token in k for token in ("PER_RUN_MAX", "MAX_HTTP_REQUESTS", "REQUEST_BUDGET_GRANTED", "REQUESTS_MAX_PER_RUN"))}
        bad_enabled = {k: v for k, v in enabled_keys.items() if str(v).lower() == "true"}
        bad_limits = {k: v for k, v in limit_keys.items() if str(v).strip() not in {"", "0", "0.0", "false", "False"}}
        rows.append({"prefix": prefix, "ok": not bad_enabled and not bad_limits, "bad_enabled": bad_enabled, "bad_limits": bad_limits})
    return rows


async def main_async() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(TIMEOUT, connect=TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        checks = await asyncio.gather(
            check_odds_api_io(client),
            check_sstats(client),
            check_bzzoiro(client),
            check_football_data(client),
            check_thesportsdb(client),
            check_weatherapi(client),
            check_open_meteo(client),
            check_clubelo(client),
        )
    non_core = non_core_env_audit()
    enabled_checks = [row for row in checks if row.get("enabled")]
    missing_optional = [row for row in checks if not row.get("enabled") and row.get("status") == "missing_key"]
    failed_enabled = [row for row in enabled_checks if not row.get("ok")]
    non_core_bad = [row for row in non_core if not row.get("ok")]
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "allowed_core_providers": ALLOWED,
        "summary": {
            "checks_total": len(checks),
            "enabled_checks": len(enabled_checks),
            "ok_enabled_checks": len([row for row in enabled_checks if row.get("ok")]),
            "failed_enabled_checks": len(failed_enabled),
            "missing_key_checks": len(missing_optional),
            "non_core_bad_env_prefixes": len(non_core_bad),
            "status": "ok" if not failed_enabled and not non_core_bad else "warning",
        },
        "checks": checks,
        "non_core_env_audit": non_core,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Provider smoke",
        "",
        f"- status: **{payload['summary']['status']}**",
        f"- enabled ok: {payload['summary']['ok_enabled_checks']}/{payload['summary']['enabled_checks']}",
        f"- missing keys: {payload['summary']['missing_key_checks']}",
        f"- non-core env violations: {payload['summary']['non_core_bad_env_prefixes']}",
        "",
        "| provider | enabled | ok | status | items | http |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in checks:
        lines.append(
            f"| {row.get('provider')} | {row.get('enabled')} | {row.get('ok')} | {row.get('status')} | "
            f"{row.get('item_count', '')} | {row.get('http_status', '')} |"
        )
    if non_core_bad:
        lines += ["", "## Non-core env violations"]
        for row in non_core_bad:
            lines.append(f"- {row['prefix']}: enabled={row['bad_enabled']} limits={row['bad_limits']}")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    if os.getenv("PROVIDER_SMOKE_FAIL_ON_ERROR", "false").lower() in {"1", "true", "yes", "on"}:
        return 1 if payload["summary"]["status"] != "ok" else 0
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
