from __future__ import annotations

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MARKERS = [
    ROOT / ".data" / "cache" / "sportlogic_daily_limit_open.json",
    ROOT / ".data" / "line_history" / "sportlogic_daily_limit_open.json",
]
REPORT = ROOT / ".data" / "exports" / "latest-sportlogic-daily-limit-guard.json"
_INSTALLED = False
_ORIG_SYNC = None
_ORIG_ASYNC = None

ZERO = {
    "SPORTLOGIC_DAILY_CIRCUIT_OPEN": "true",
    "SPORTLOGIC_ENABLED": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
    "SPORTLOGIC_MATCH_LIMIT": "0",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES": "0",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT": "0",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "0",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "0",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _is_sl(url: Any) -> bool:
    text = str(url or "").lower()
    return "sportlogic" in text or "api.sportlogic.io" in text


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _open_marker() -> dict[str, Any] | None:
    for path in MARKERS:
        payload = _read(path)
        if payload.get("status") == "open" and payload.get("date_utc") == _today():
            return payload
    return None


def _github_env(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def _without_sportlogic(value: str) -> str:
    parts = []
    for raw in str(value or "").split(','):
        item = raw.strip()
        if item and item.lower() != "sportlogic":
            parts.append(item)
    return ','.join(parts)


def _zero(reason: str, marker: dict[str, Any] | None = None) -> None:
    env = dict(ZERO)
    env["SPORTLOGIC_DAILY_CIRCUIT_REASON"] = reason
    for key in ("PROVIDER_SMOKE_FAST_PROVIDERS", "PROVIDER_SMOKE_MATCHING_PROVIDERS"):
        current = os.getenv(key)
        if current:
            env[key] = _without_sportlogic(current)
    allowed = os.getenv("HARIZON_ALLOWED_PROVIDER_SET")
    if allowed:
        env["HARIZON_ALLOWED_PROVIDER_SET"] = _without_sportlogic(allowed)
    for key, value in env.items():
        os.environ[key] = value
    _github_env(env)
    _write(REPORT, {
        "status": "open",
        "date_utc": _today(),
        "reason": reason,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "marker": marker or {},
        "provider_lists": {k: os.getenv(k) for k in ("PROVIDER_SMOKE_FAST_PROVIDERS", "PROVIDER_SMOKE_MATCHING_PROVIDERS", "HARIZON_ALLOWED_PROVIDER_SET")},
    })


def _mark(reason: str, text: str = "", status_code: int | None = None, url: str = "") -> None:
    marker = {
        "status": "open",
        "date_utc": _today(),
        "reason": reason,
        "opened_at_utc": datetime.now(timezone.utc).isoformat(),
        "status_code": status_code,
        "url": str(url)[:300],
        "body_preview": str(text or "")[:1000],
    }
    for path in MARKERS:
        _write(path, marker)
    _zero(reason, marker)


def _is_daily_429(status_code: int, text: str) -> bool:
    low = str(text or "").lower()
    return int(status_code or 0) == 429 and ("daily limit" in low or "limit of 500" in low or "rate_limit_exceeded" in low or "requests exceeded" in low)


def install() -> None:
    global _INSTALLED, _ORIG_SYNC, _ORIG_ASYNC
    marker = _open_marker()
    if marker:
        _zero("existing_marker", marker)
    atexit.register(lambda: _zero("existing_marker", _open_marker()) if _open_marker() else None)
    if _INSTALLED:
        return
    _INSTALLED = True
    try:
        import httpx
    except Exception:
        return
    _ORIG_SYNC = _ORIG_SYNC or httpx.Client.request
    _ORIG_ASYNC = _ORIG_ASYNC or httpx.AsyncClient.request

    def sync_request(self, method, url, *args, **kwargs):
        response = _ORIG_SYNC(self, method, url, *args, **kwargs)
        if _is_sl(url):
            try:
                text = response.text
            except Exception:
                text = ""
            if _is_daily_429(getattr(response, "status_code", 0), text):
                _mark("sportlogic_daily_429", text, getattr(response, "status_code", None), str(url))
        return response

    async def async_request(self, method, url, *args, **kwargs):
        response = await _ORIG_ASYNC(self, method, url, *args, **kwargs)
        if _is_sl(url):
            try:
                text = response.text
            except Exception:
                text = ""
            if _is_daily_429(getattr(response, "status_code", 0), text):
                _mark("sportlogic_daily_429", text, getattr(response, "status_code", None), str(url))
        return response

    httpx.Client.request = sync_request
    httpx.AsyncClient.request = async_request
