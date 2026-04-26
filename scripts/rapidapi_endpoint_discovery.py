from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

UTC = timezone.utc

STATE_PATH = Path(".data/provider_quota_state.json")
OUT_PATH = Path(".data/exports/latest-rapidapi-endpoint-discovery.json")
OUT_SUMMARY = Path(".data/exports/latest-rapidapi-endpoint-discovery-summary.json")


@dataclass(frozen=True)
class EndpointCandidate:
    provider: str
    host: str
    key_env: str | None
    label: str
    url: str
    expected_use: str


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


def today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def rapidapi_key_for(candidate: EndpointCandidate) -> str:
    names: list[str] = []
    if candidate.key_env:
        names.append(candidate.key_env)
    names.extend(["RAPIDAPI_KEY", "X_RAPIDAPI_KEY"])
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def provider_limit(provider: str) -> int:
    mapping = {
        "free_live_football_data": "RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS",
        "sportapi7": "RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS",
        "sportsbook_api": "RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS",
        "odds_feed": "RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS",
    }
    return env_int(mapping.get(provider, "RAPIDAPI_DEFAULT_DAILY_LIMIT"), 1)


def can_call(state: dict[str, Any], candidate: EndpointCandidate) -> tuple[bool, str]:
    if not env_bool("RAPIDAPI_ENDPOINT_DISCOVERY_ENABLED", True):
        return False, "discovery_disabled"
    if not rapidapi_key_for(candidate):
        return False, "missing_rapidapi_key"

    day = today_key()
    provider_state = state.setdefault("providers", {}).setdefault(f"discovery::{candidate.provider}", {})
    usage = provider_state.setdefault("usage", {}).setdefault(day, {"requests": 0, "errors": 0})
    limit = provider_limit(candidate.provider)
    if int(usage.get("requests") or 0) >= limit:
        return False, f"provider_daily_limit_reached:{limit}"

    global_state = state.setdefault("providers", {}).setdefault("discovery::global", {})
    global_usage = global_state.setdefault("usage", {}).setdefault(day, {"requests": 0, "errors": 0})
    global_limit = env_int("RAPIDAPI_DISCOVERY_MAX_CALLS_TOTAL", 16)
    if int(global_usage.get("requests") or 0) >= global_limit:
        return False, f"global_daily_limit_reached:{global_limit}"

    cooldown_until = str(provider_state.get("cooldown_until") or "")
    if cooldown_until:
        try:
            cooldown_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if cooldown_dt > datetime.now(UTC):
                return False, "cooldown_active"
        except Exception:
            pass
    return True, "ok"


def mark_usage(state: dict[str, Any], candidate: EndpointCandidate, *, error: bool, status_code: int | None) -> None:
    day = today_key()
    for key in (f"discovery::{candidate.provider}", "discovery::global"):
        provider_state = state.setdefault("providers", {}).setdefault(key, {})
        usage = provider_state.setdefault("usage", {}).setdefault(day, {"requests": 0, "errors": 0})
        usage["requests"] = int(usage.get("requests") or 0) + 1
        if error:
            usage["errors"] = int(usage.get("errors") or 0) + 1
        provider_state["last_status_code"] = status_code
        provider_state["last_checked_at"] = datetime.now(UTC).isoformat()
        if status_code == 429:
            provider_state["cooldown_until"] = datetime.now(UTC).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()


def shape_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        result: dict[str, Any] = {"type": "dict", "keys": list(payload.keys())[:40]}
        for key in ("data", "response", "results", "events", "matches", "fixtures", "leagues", "tournaments", "markets", "items", "suggestions"):
            value = payload.get(key)
            if isinstance(value, list):
                result[f"{key}_len"] = len(value)
                if value and isinstance(value[0], dict):
                    result[f"{key}_first_keys"] = list(value[0].keys())[:30]
            elif isinstance(value, dict):
                result[f"{key}_keys"] = list(value.keys())[:30]
        return result
    if isinstance(payload, list):
        result = {"type": "list", "len": len(payload)}
        if payload and isinstance(payload[0], dict):
            result["first_keys"] = list(payload[0].keys())[:30]
        return result
    return {"type": type(payload).__name__}


def safe_preview(payload: Any, max_chars: int = 1400) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
    except Exception:
        return "<preview_unavailable>"
    return text[:max_chars] + ("...<truncated>" if len(text) > max_chars else "")


def looks_useful_for_football(payload: Any) -> tuple[bool, list[str]]:
    flags: list[str] = []
    text = ""
    try:
        text = json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        text = str(payload).lower()

    for token in ("football", "soccer", "fixture", "fixtures", "match", "matches", "event", "events", "standing", "standings", "team", "league", "tournament", "odds", "market"):
        if token in text:
            flags.append(token)
    return bool(flags), sorted(set(flags))


def date_strings() -> dict[str, str]:
    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)
    return {
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "today_compact": today.strftime("%Y%m%d"),
    }


def build_candidates() -> list[EndpointCandidate]:
    d = date_strings()

    free_host = "free-api-live-football-data.p.rapidapi.com"
    free_base = f"https://{free_host}"
    free_paths = [
        ("live matches", "/football-live-matches", "live football matches"),
        ("matches today", "/football-matches-today", "today fixtures/matches"),
        ("matches by date", f"/football-matches?date={d['today']}", "fixtures by date"),
        ("fixtures", f"/football-fixtures?date={d['today']}", "fixtures by date"),
        ("leagues", "/football-leagues", "league list"),
        ("teams search", "/football-teams-search?search=manchester", "team search/schema"),
        ("standings", "/football-standings", "standings/schema"),
        ("player search control", "/football-players-search?search=m", "known working endpoint control"),
    ]

    sportapi_host = "sportapi7.p.rapidapi.com"
    sportapi_base = f"https://{sportapi_host}"
    sportapi_paths = [
        ("football scheduled today", f"/api/v1/sport/football/scheduled-events/{d['today']}", "football scheduled events"),
        ("football scheduled tomorrow", f"/api/v1/sport/football/scheduled-events/{d['tomorrow']}", "football scheduled events"),
        ("football live", "/api/v1/sport/football/events/live", "football live events"),
        ("football categories", "/api/v1/sport/football/categories", "football categories"),
        ("football unique tournaments", "/api/v1/sport/football/unique-tournaments", "football tournaments"),
    ]

    sportsbook_host = "sportsbook-api2.p.rapidapi.com"
    sportsbook_base = f"https://{sportsbook_host}"
    sportsbook_paths = [
        ("sports list", "/v0/sports", "sports list"),
        ("soccer events", "/v0/events?sport=SOCCER", "soccer events"),
        ("football events", "/v0/events?sport=FOOTBALL", "football events"),
        ("soccer arbitrage", "/v0/advantages/?type=ARBITRAGE&sport=SOCCER", "soccer advantages"),
        ("arbitrage control", "/v0/advantages/?type=ARBITRAGE", "known working endpoint control"),
    ]

    odds_feed_host = "odds-feed.p.rapidapi.com"
    odds_base = f"https://{odds_feed_host}"
    odds_paths = [
        ("sports", "/api/v1/sports", "sports list"),
        ("events", "/api/v1/events", "event list"),
        ("football events", "/api/v1/events?sport=football", "football event list"),
        ("soccer events", "/api/v1/events?sport=soccer", "soccer event list"),
        ("competitions", "/api/v1/competitions", "competition list"),
    ]

    out: list[EndpointCandidate] = []
    for label, path, use in free_paths:
        out.append(EndpointCandidate("free_live_football_data", free_host, "FREE_FOOTBALL_RAPIDAPI_KEY", label, free_base + path, use))
    for label, path, use in sportapi_paths:
        out.append(EndpointCandidate("sportapi7", sportapi_host, "SPORTAPI7_RAPIDAPI_KEY", label, sportapi_base + path, use))
    for label, path, use in sportsbook_paths:
        out.append(EndpointCandidate("sportsbook_api", sportsbook_host, "SPORTSBOOK_RAPIDAPI_KEY", label, sportsbook_base + path, use))
    for label, path, use in odds_paths:
        out.append(EndpointCandidate("odds_feed", odds_feed_host, "ODDS_FEED_RAPIDAPI_KEY", label, odds_base + path, use))
    return out


async def probe_endpoint(client: httpx.AsyncClient, state: dict[str, Any], candidate: EndpointCandidate) -> dict[str, Any]:
    allowed, reason = can_call(state, candidate)
    result: dict[str, Any] = {
        "provider": candidate.provider,
        "label": candidate.label,
        "host": candidate.host,
        "url": candidate.url,
        "expected_use": candidate.expected_use,
        "called": False,
        "skip_reason": None if allowed else reason,
        "ok": False,
        "status_code": None,
    }
    if not allowed:
        return result

    key = rapidapi_key_for(candidate)
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": candidate.host,
        "x-rapidapi-key": key,
    }

    try:
        response = await client.get(candidate.url, headers=headers)
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
        useful, flags = looks_useful_for_football(payload)
        result["looks_useful_for_football"] = useful
        result["useful_flags"] = flags
        result["preview"] = safe_preview(payload)
        if response.status_code in {401, 403}:
            result["auth_failed"] = True
        if response.status_code == 404:
            result["not_found"] = True
        if response.status_code == 429:
            result["rate_limited"] = True
        mark_usage(state, candidate, error=not result["ok"], status_code=response.status_code)
    except Exception as exc:
        result["called"] = True
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        mark_usage(state, candidate, error=True, status_code=None)
    return result


async def main_async() -> int:
    state = load_json(STATE_PATH, {"providers": {}})
    candidates = build_candidates()
    timeout = float(os.getenv("RAPIDAPI_ENDPOINT_DISCOVERY_TIMEOUT_SECONDS") or 12.0)

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for candidate in candidates:
            results.append(await probe_endpoint(client, state, candidate))

    write_json(STATE_PATH, state)

    useful = [
        item for item in results
        if item.get("ok") and item.get("looks_useful_for_football")
    ]
    callable_ok = [item for item in results if item.get("ok")]
    errors = [item for item in results if item.get("called") and not item.get("ok")]

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "candidates_total": len(results),
        "called": sum(1 for item in results if item.get("called")),
        "ok": len(callable_ok),
        "useful_football_like": len(useful),
        "errors": len(errors),
        "skipped": sum(1 for item in results if not item.get("called")),
        "top_useful_endpoints": [
            {
                "provider": item.get("provider"),
                "label": item.get("label"),
                "url": item.get("url"),
                "status_code": item.get("status_code"),
                "shape": item.get("shape"),
                "useful_flags": item.get("useful_flags"),
            }
            for item in useful[:20]
        ],
        "ok_endpoints": [
            {
                "provider": item.get("provider"),
                "label": item.get("label"),
                "url": item.get("url"),
                "shape": item.get("shape"),
            }
            for item in callable_ok[:30]
        ],
        "error_endpoints": [
            {
                "provider": item.get("provider"),
                "label": item.get("label"),
                "url": item.get("url"),
                "status_code": item.get("status_code"),
                "skip_reason": item.get("skip_reason"),
                "shape": item.get("shape"),
            }
            for item in errors[:30]
        ],
        "state_path": str(STATE_PATH),
        "full_output": str(OUT_PATH),
    }

    write_json(OUT_PATH, {"created_at": summary["created_at"], "results": results})
    write_json(OUT_SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    import asyncio
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
