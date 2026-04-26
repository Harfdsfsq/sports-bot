from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

UTC = timezone.utc

STATE_PATH = Path(".data/provider_quota_state.json")
OUT_PATH = Path(".data/exports/latest-rapidapi-provider-probe.json")
OUT_SUMMARY = Path(".data/exports/latest-rapidapi-provider-summary.json")


@dataclass
class RapidApiProbe:
    key: str
    name: str
    host: str
    url: str
    enabled_env: str
    daily_limit_env: str
    key_env: str | None = None
    requires_event_ids: bool = False


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name) or default))
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rapidapi_key_for(probe: RapidApiProbe) -> str:
    candidates = []
    if probe.key_env:
        candidates.append(probe.key_env)
    candidates.extend([
        "RAPIDAPI_KEY",
        "X_RAPIDAPI_KEY",
    ])
    for name in candidates:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def can_call(state: dict[str, Any], probe: RapidApiProbe) -> tuple[bool, str]:
    if not env_bool("RAPIDAPI_PROBE_ENABLED", True):
        return False, "rapidapi_probe_disabled"
    if not env_bool(probe.enabled_env, True):
        return False, "provider_probe_disabled"

    key = rapidapi_key_for(probe)
    if not key:
        return False, "missing_rapidapi_key"

    if probe.requires_event_ids and not str(os.getenv("RAPIDAPI_ODDS_FEED_EVENT_IDS") or "").strip():
        return False, "missing_event_ids"

    day = today_key()
    provider_state = state.setdefault("providers", {}).setdefault(probe.key, {})
    usage = provider_state.setdefault("usage", {}).setdefault(day, {"requests": 0, "errors": 0})
    limit = env_int(probe.daily_limit_env, env_int("RAPIDAPI_DEFAULT_DAILY_LIMIT", 1))
    if int(usage.get("requests") or 0) >= limit:
        return False, f"daily_limit_reached:{limit}"
    cooldown_until = str(provider_state.get("cooldown_until") or "")
    if cooldown_until:
        try:
            cooldown_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if cooldown_dt > datetime.now(UTC):
                return False, "cooldown_active"
        except Exception:
            pass
    return True, "ok"


def mark_usage(state: dict[str, Any], probe: RapidApiProbe, *, error: bool, status_code: int | None) -> None:
    day = today_key()
    provider_state = state.setdefault("providers", {}).setdefault(probe.key, {})
    usage = provider_state.setdefault("usage", {}).setdefault(day, {"requests": 0, "errors": 0})
    usage["requests"] = int(usage.get("requests") or 0) + 1
    if error:
        usage["errors"] = int(usage.get("errors") or 0) + 1
    provider_state["last_status_code"] = status_code
    provider_state["last_checked_at"] = datetime.now(UTC).isoformat()
    if status_code == 429:
        provider_state["cooldown_until"] = datetime.now(UTC).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()


def safe_preview(payload: Any, max_chars: int = 1800) -> Any:
    try:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload)
        if len(text) > max_chars:
            text = text[:max_chars] + "...<truncated>"
        try:
            return json.loads(text.replace("...<truncated>", ""))
        except Exception:
            return text
    except Exception:
        return "<preview_unavailable>"


def shape_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        keys = list(payload.keys())[:30]
        result: dict[str, Any] = {"type": "dict", "keys": keys}
        for key in ("data", "response", "results", "events", "markets", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                result[f"{key}_len"] = len(value)
            elif isinstance(value, dict):
                result[f"{key}_keys"] = list(value.keys())[:20]
        return result
    if isinstance(payload, list):
        return {"type": "list", "len": len(payload), "first_type": type(payload[0]).__name__ if payload else None}
    return {"type": type(payload).__name__}


def _walk_event_ids(payload: Any, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(payload, dict):
        for key in (
            "event_id",
            "eventId",
            "provider_event_id",
            "odds_api_io_event_id",
            "external_event_id",
            "id",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                text = str(value).strip()
                if text and text not in out:
                    out.append(text)
                    if len(out) >= limit:
                        return
        for value in payload.values():
            _walk_event_ids(value, out, limit)
            if len(out) >= limit:
                return
    elif isinstance(payload, list):
        for item in payload:
            _walk_event_ids(item, out, limit)
            if len(out) >= limit:
                return


def discover_odds_feed_event_ids(limit: int = 20) -> str:
    explicit = str(os.getenv("RAPIDAPI_ODDS_FEED_EVENT_IDS") or "").strip()
    if explicit:
        return explicit

    candidates = [
        Path(".data/exports/latest-match-data-coverage-matches.json"),
        Path(".data/exports/latest-rescue-candidates.json"),
        Path("artifacts/run-bot/latest-rescue-candidates.json"),
        Path(".logs/debug-last-run.json"),
    ]
    ids: list[str] = []
    for path in candidates:
        payload = load_json(path, None)
        if payload is None:
            continue
        _walk_event_ids(payload, ids, limit)
        if len(ids) >= limit:
            break
    return ",".join(ids[:limit])


def build_probes() -> list[RapidApiProbe]:
    odds_feed_ids = quote(discover_odds_feed_event_ids(env_int("RAPIDAPI_ODDS_FEED_EVENT_IDS_LIMIT", 20)), safe=",")
    odds_feed_url = (
        "https://odds-feed.p.rapidapi.com/api/v1/markets/feed"
        f"?placing=LIVE&market_name=1X2&bet_type=BACK&page=0&event_ids={odds_feed_ids}&period=FULL_TIME_AND_OT"
    )
    return [
        RapidApiProbe(
            key="sportsbook_api",
            name="Sportsbook API advantages",
            host="sportsbook-api2.p.rapidapi.com",
            url="https://sportsbook-api2.p.rapidapi.com/v0/advantages/?type=ARBITRAGE",
            enabled_env="RAPIDAPI_SPORTSBOOK_PROBE_ENABLED",
            daily_limit_env="RAPIDAPI_SPORTSBOOK_DAILY_LIMIT",
            key_env="SPORTSBOOK_RAPIDAPI_KEY",
        ),
        RapidApiProbe(
            key="odds_feed",
            name="Odds Feed live market feed",
            host="odds-feed.p.rapidapi.com",
            url=odds_feed_url,
            enabled_env="RAPIDAPI_ODDS_FEED_PROBE_ENABLED",
            daily_limit_env="RAPIDAPI_ODDS_FEED_DAILY_LIMIT",
            key_env="ODDS_FEED_RAPIDAPI_KEY",
            requires_event_ids=True,
        ),
        RapidApiProbe(
            key="free_live_football_data",
            name="Free API Live Football Data player search",
            host="free-api-live-football-data.p.rapidapi.com",
            url="https://free-api-live-football-data.p.rapidapi.com/football-players-search?search=m",
            enabled_env="RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED",
            daily_limit_env="RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT",
            key_env="FREE_FOOTBALL_RAPIDAPI_KEY",
        ),
        RapidApiProbe(
            key="sportapi7",
            name="SportAPI sample endpoint",
            host="sportapi7.p.rapidapi.com",
            url=str(os.getenv("RAPIDAPI_SPORTAPI7_PROBE_URL") or "https://sportapi7.p.rapidapi.com/api/v1/event/15508283/atbat/983367/pitches"),
            enabled_env="RAPIDAPI_SPORTAPI7_PROBE_ENABLED",
            daily_limit_env="RAPIDAPI_SPORTAPI7_DAILY_LIMIT",
            key_env="SPORTAPI7_RAPIDAPI_KEY",
        ),
    ]


async def probe_one(client: httpx.AsyncClient, state: dict[str, Any], probe: RapidApiProbe) -> dict[str, Any]:
    allowed, reason = can_call(state, probe)
    result: dict[str, Any] = {
        "provider": probe.key,
        "name": probe.name,
        "host": probe.host,
        "enabled": env_bool(probe.enabled_env, True),
        "called": False,
        "skip_reason": None if allowed else reason,
        "status_code": None,
        "ok": False,
        "rate_limit_headers": {},
        "shape": {},
    }
    if not allowed:
        return result

    key = rapidapi_key_for(probe)
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": probe.host,
        "x-rapidapi-key": key,
    }
    try:
        response = await client.get(probe.url, headers=headers)
        result["called"] = True
        result["status_code"] = response.status_code
        result["ok"] = 200 <= response.status_code < 300
        result["rate_limit_headers"] = {
            name: value
            for name, value in response.headers.items()
            if name.lower().startswith("x-ratelimit") or name.lower() in {"retry-after"}
        }
        try:
            payload = response.json()
        except Exception:
            payload = response.text
        result["shape"] = shape_summary(payload)
        result["preview"] = safe_preview(payload)
        if response.status_code in {401, 403}:
            result["auth_failed"] = True
        if response.status_code == 429:
            result["rate_limited"] = True
        mark_usage(state, probe, error=not result["ok"], status_code=response.status_code)
        return result
    except Exception as exc:
        result["called"] = True
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        mark_usage(state, probe, error=True, status_code=None)
        return result


async def main_async() -> int:
    enabled = env_bool("RAPIDAPI_PROBE_ENABLED", True)
    state = load_json(STATE_PATH, {"providers": {}})
    probes = build_probes()
    timeout = float(os.getenv("RAPIDAPI_PROBE_TIMEOUT_SECONDS") or 12.0)
    results: list[dict[str, Any]] = []
    if not enabled:
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "enabled": False,
            "reason": "RAPIDAPI_PROBE_ENABLED=false",
            "providers": [],
        }
        write_json(OUT_PATH, payload)
        write_json(OUT_SUMMARY, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        for probe in probes:
            results.append(await probe_one(client, state, probe))

    write_json(STATE_PATH, state)

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": True,
        "providers_total": len(results),
        "called": sum(1 for item in results if item.get("called")),
        "ok": sum(1 for item in results if item.get("ok")),
        "skipped": sum(1 for item in results if not item.get("called")),
        "rate_limited": sum(1 for item in results if item.get("rate_limited")),
        "auth_failed": sum(1 for item in results if item.get("auth_failed")),
        "results": results,
        "state_path": str(STATE_PATH),
        "odds_feed_event_ids_discovered": bool(str(os.getenv("RAPIDAPI_ODDS_FEED_EVENT_IDS") or discover_odds_feed_event_ids()).strip()),
    }
    write_json(OUT_PATH, summary)
    write_json(OUT_SUMMARY, {
        key: summary[key]
        for key in ("created_at", "enabled", "providers_total", "called", "ok", "skipped", "rate_limited", "auth_failed", "state_path", "odds_feed_event_ids_discovered")
    } | {
        "provider_status": [
            {
                "provider": item.get("provider"),
                "called": item.get("called"),
                "ok": item.get("ok"),
                "skip_reason": item.get("skip_reason"),
                "status_code": item.get("status_code"),
                "shape": item.get("shape"),
            }
            for item in results
        ]
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    import asyncio
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
