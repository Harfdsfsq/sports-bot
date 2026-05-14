from __future__ import annotations

"""SportLogic daily quota circuit breaker.

SportLogic free quota is 500 requests/day. If any process receives a daily-limit
429 response, this module writes a marker for the current UTC date and disables
SportLogic budgets for the rest of that day.  It prevents repeated smoke/runtime
steps from burning requests after the account is already exhausted.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MARKER = ROOT / ".data" / "cache" / "sportlogic_daily_circuit.json"
REPORT = ROOT / ".data" / "exports" / "latest-sportlogic-daily-circuit-breaker.json"
_INSTALLED = False
_ORIG_HTTPX_REQUEST = None
_ORIG_HTTPX_ASYNC_REQUEST = None

ZERO_ENV = {
    "SPORTLOGIC_DAILY_CIRCUIT_OPEN": "true",
    "SPORTLOGIC_ENABLED": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES": "0",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT": "0",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "0",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "0",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
}


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _is_sportlogic_url(url: Any) -> bool:
    text = str(url or "").lower()
    return "sportlogic" in text or "api.sportlogic.io" in text


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _append_github_env(payload: dict[str, str]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key in sorted(payload):
                fh.write(f"{key}={payload[key]}\n")
    except Exception:
        pass


def _marker_open() -> dict[str, Any] | None:
    payload = _load_json(MARKER, {})
    if not isinstance(payload, dict):
        return None
    if payload.get("date_utc") == _utc_day() and payload.get("status") == "open":
        return payload
    return None


def _apply_zero_env(reason: str, marker: dict[str, Any] | None = None) -> None:
    for key, value in ZERO_ENV.items():
        os.environ[key] = value
    os.environ["SPORTLOGIC_DAILY_CIRCUIT_REASON"] = reason
    _append_github_env({**ZERO_ENV, "SPORTLOGIC_DAILY_CIRCUIT_REASON": reason})
    report = {
        "status": "open",
        "reason": reason,
        "date_utc": _utc_day(),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "marker": marker or {},
        "env_zeroed": sorted(ZERO_ENV.keys()),
    }
    _write_json(REPORT, report)


def _open_marker(reason: str, *, response_text: str = "", status_code: int | None = None, url: str = "") -> None:
    used = None
    limit = None
    m = re.search(r"Used:\s*(\d+).*?limit(?: of)?\s*(\d+)|limit(?: of)?\s*(\d+).*?Used:\s*(\d+)", response_text, re.I | re.S)
    if m:
        nums = [int(x) for x in m.groups() if x]
        if len(nums) >= 2:
            used, limit = nums[0], nums[1]
    marker = {
        "status": "open",
        "reason": reason,
        "date_utc": _utc_day(),
        "opened_at_utc": datetime.now(timezone.utc).isoformat(),
        "status_code": status_code,
        "url": url[:300],
        "used": used,
        "limit": limit,
        "body_preview": response_text[:1000],
    }
    _write_json(MARKER, marker)
    _apply_zero_env(reason, marker)


def _looks_daily_limit_response(status_code: int, text: str) -> bool:
    if int(status_code or 0) != 429:
        return False
    low = str(text or "").lower()
    return "daily limit" in low or "rate_limit_exceeded" in low or "limit of 500" in low


def _synthetic_blocked_response(httpx_module: Any, request: Any = None):
    body = {
        "success": False,
        "error": {
            "code": "SPORTLOGIC_DAILY_CIRCUIT_OPEN",
            "message": "SportLogic daily quota circuit is open for the current UTC day; request skipped.",
        },
    }
    try:
        return httpx_module.Response(429, json=body, request=request)
    except Exception:
        return httpx_module.Response(429, json=body)


def install() -> None:
    global _INSTALLED, _ORIG_HTTPX_REQUEST, _ORIG_HTTPX_ASYNC_REQUEST
    if _INSTALLED:
        return
    _INSTALLED = True
    marker = _marker_open()
    if marker:
        _apply_zero_env("existing_daily_circuit_marker", marker)
    try:
        import httpx
    except Exception:
        return
    if _ORIG_HTTPX_REQUEST is None:
        _ORIG_HTTPX_REQUEST = httpx.Client.request
    if _ORIG_HTTPX_ASYNC_REQUEST is None:
        _ORIG_HTTPX_ASYNC_REQUEST = httpx.AsyncClient.request

    def request_wrapper(self, method, url, *args, **kwargs):
        if _is_sportlogic_url(url) and _marker_open():
            try:
                req = self.build_request(method, url, **{k: v for k, v in kwargs.items() if k in {"headers", "params", "content", "data", "json"}})
            except Exception:
                req = None
            return _synthetic_blocked_response(httpx, req)
        response = _ORIG_HTTPX_REQUEST(self, method, url, *args, **kwargs)
        if _is_sportlogic_url(url):
            try:
                text = response.text
            except Exception:
                text = ""
            if _looks_daily_limit_response(getattr(response, "status_code", 0), text):
                _open_marker("sportlogic_daily_limit_response", response_text=text, status_code=getattr(response, "status_code", None), url=str(url))
        return response

    async def async_request_wrapper(self, method, url, *args, **kwargs):
        if _is_sportlogic_url(url) and _marker_open():
            try:
                req = self.build_request(method, url, **{k: v for k, v in kwargs.items() if k in {"headers", "params", "content", "data", "json"}})
            except Exception:
                req = None
            return _synthetic_blocked_response(httpx, req)
        response = await _ORIG_HTTPX_ASYNC_REQUEST(self, method, url, *args, **kwargs)
        if _is_sportlogic_url(url):
            try:
                text = response.text
            except Exception:
                text = ""
            if _looks_daily_limit_response(getattr(response, "status_code", 0), text):
                _open_marker("sportlogic_daily_limit_response", response_text=text, status_code=getattr(response, "status_code", None), url=str(url))
        return response

    httpx.Client.request = request_wrapper
    httpx.AsyncClient.request = async_request_wrapper
