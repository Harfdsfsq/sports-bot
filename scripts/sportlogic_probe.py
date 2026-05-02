from __future__ import annotations

"""Low-cost SportLogic runtime probe.

The bot needs enough visibility to understand why SportLogic produces no odds
or contexts. This script intentionally performs a tiny number of requests,
records endpoint shapes, and never fails the workflow unless explicitly forced.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_PATH = Path(".data/exports/latest-sportlogic-probe.json")
STATE_PATH = Path(".data/provider_quota_state.json")
DEBUG_PATHS = [
    Path(".data/exports/latest-sportlogic-debug.json"),
    Path(".data/exports/latest-sportlogic-odds-sample.json"),
    Path(".logs/debug-last-run.json"),
]


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def api_key() -> str:
    for name in ("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"):
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def headers() -> dict[str, str]:
    key = api_key()
    result = {"Accept": "application/json"}
    if not key:
        return result
    header_name = str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key").strip() or "X-API-Key"
    if header_name.lower() == "authorization":
        scheme = str(os.getenv("SPORTLOGIC_AUTH_SCHEME") or "Bearer").strip()
        result["Authorization"] = f"{scheme} {key}".strip()
    else:
        result[header_name] = key
    return result


def safe_preview(payload: Any, max_chars: int = 1800) -> Any:
    try:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload)
        if len(text) > max_chars:
            text = text[:max_chars] + "...<truncated>"
        return text
    except Exception:
        return "<preview_unavailable>"


def shape_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        out: dict[str, Any] = {"type": "dict", "keys": list(payload.keys())[:40]}
        for key in ("data", "response", "results", "fixtures", "matches", "events", "items", "odds", "markets"):
            value = payload.get(key)
            if isinstance(value, list):
                out[f"{key}_len"] = len(value)
                if value and isinstance(value[0], dict):
                    out[f"{key}_first_keys"] = list(value[0].keys())[:30]
            elif isinstance(value, dict):
                out[f"{key}_keys"] = list(value.keys())[:30]
        return out
    if isinstance(payload, list):
        out = {"type": "list", "len": len(payload)}
        if payload and isinstance(payload[0], dict):
            out["first_keys"] = list(payload[0].keys())[:30]
        return out
    return {"type": type(payload).__name__}


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "response", "results", "fixtures", "matches", "events", "items", "odds", "markets"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = extract_rows(value)
            if nested:
                return nested
    return []


def find_event_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("event_id", "eventId", "game_id", "gameId", "fixture_id", "fixtureId", "id"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for value in payload.values():
            found = find_event_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_event_id(item)
            if found:
                return found
    return ""


def discover_event_id() -> str:
    explicit = str(os.getenv("SPORTLOGIC_PROBE_EVENT_ID") or "").strip()
    if explicit:
        return explicit
    for path in DEBUG_PATHS:
        payload = load_json(path, None)
        if payload is None:
            continue
        found = find_event_id(payload)
        if found:
            return found
    return ""


def today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def can_call(state: dict[str, Any]) -> tuple[bool, str]:
    if not env_bool("SPORTLOGIC_PROBE_ENABLED", True):
        return False, "sportlogic_probe_disabled"
    if not env_bool("ENABLE_SPORTLOGIC", True) or not env_bool("SPORTLOGIC_ENABLED", True):
        return False, "sportlogic_disabled"
    if not api_key():
        return False, "missing_sportlogic_api_key"
    daily_limit = env_int("SPORTLOGIC_PROBE_DAILY_LIMIT", 3)
    provider_state = state.setdefault("providers", {}).setdefault("sportlogic_probe", {})
    usage = provider_state.setdefault("usage", {}).setdefault(today_key(), {"requests": 0, "errors": 0})
    if int(usage.get("requests") or 0) >= daily_limit:
        return False, f"daily_limit_reached:{daily_limit}"
    return True, "ok"


def mark_usage(state: dict[str, Any], *, status_code: int | None, error: bool) -> None:
    provider_state = state.setdefault("providers", {}).setdefault("sportlogic_probe", {})
    usage = provider_state.setdefault("usage", {}).setdefault(today_key(), {"requests": 0, "errors": 0})
    usage["requests"] = int(usage.get("requests") or 0) + 1
    if error:
        usage["errors"] = int(usage.get("errors") or 0) + 1
    provider_state["last_status_code"] = status_code
    provider_state["last_checked_at"] = datetime.now(UTC).isoformat()


async def call_endpoint(client: httpx.AsyncClient, state: dict[str, Any], base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    result: dict[str, Any] = {
        "path": path,
        "params": dict(params or {}),
        "called": False,
        "ok": False,
        "status_code": None,
        "shape": {},
        "rate_limit_headers": {},
    }
    try:
        response = await client.get(url, headers=headers(), params=params or None)
        result["called"] = True
        result["status_code"] = response.status_code
        result["ok"] = 200 <= response.status_code < 300
        result["rate_limit_headers"] = {
            name: value
            for name, value in response.headers.items()
            if name.lower().startswith("x-ratelimit") or name.lower() in {"retry-after"}
        }
        try:
            payload: Any = response.json()
        except Exception:
            payload = response.text
        result["shape"] = shape_summary(payload)
        result["preview"] = safe_preview(payload)
        result["rows_detected"] = len(extract_rows(payload))
        found_event_id = find_event_id(payload)
        if found_event_id:
            result["sample_event_id"] = found_event_id
        if response.status_code in {401, 403}:
            result["auth_failed"] = True
        if response.status_code == 429:
            result["rate_limited"] = True
        mark_usage(state, status_code=response.status_code, error=not result["ok"])
        return result
    except Exception as exc:
        result["called"] = True
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        mark_usage(state, status_code=None, error=True)
        return result


async def main_async() -> int:
    state = load_json(STATE_PATH, {"providers": {}})
    allowed, reason = can_call(state)
    base_url = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    timeout = float(os.getenv("SPORTLOGIC_PROBE_TIMEOUT_SECONDS") or os.getenv("SPORTLOGIC_TIMEOUT_SECONDS") or 20.0)
    today = datetime.now(UTC).date().isoformat()
    payload: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": env_bool("SPORTLOGIC_PROBE_ENABLED", True),
        "base_url": base_url,
        "api_key_present": bool(api_key()),
        "called": False,
        "skip_reason": None if allowed else reason,
        "results": [],
    }
    if not allowed:
        write_json(OUT_PATH, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    max_calls = max(1, env_int("SPORTLOGIC_PROBE_MAX_CALLS", 3))
    endpoints: list[tuple[str, dict[str, Any]]] = [
        ("/games", {"date_from": today, "date_to": today, "per_page": 25}),
    ]
    event_id = discover_event_id()
    if event_id:
        endpoints.extend([
            (f"/games/{event_id}/odds", {}),
            ("/odds", {"game_id": event_id}),
        ])

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for path, params in endpoints[:max_calls]:
            item = await call_endpoint(client, state, base_url, path, params)
            payload["results"].append(item)
            if not event_id and item.get("sample_event_id"):
                event_id = str(item.get("sample_event_id") or "")
                if len(payload["results"]) < max_calls:
                    payload["results"].append(await call_endpoint(client, state, base_url, f"/games/{event_id}/odds", {}))
                    if len(payload["results"]) < max_calls:
                        payload["results"].append(await call_endpoint(client, state, base_url, "/odds", {"game_id": event_id}))
                break

    payload["called"] = any(item.get("called") for item in payload["results"])
    payload["ok"] = sum(1 for item in payload["results"] if item.get("ok"))
    payload["auth_failed"] = any(item.get("auth_failed") for item in payload["results"])
    payload["rate_limited"] = any(item.get("rate_limited") for item in payload["results"])
    payload["event_id_used"] = event_id or None
    write_json(STATE_PATH, state)
    write_json(OUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
