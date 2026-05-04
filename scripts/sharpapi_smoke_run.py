from __future__ import annotations

"""Standalone SharpAPI smoke probe.

This script intentionally does not instantiate PredictionRunner and does not
bootstrap matches from odds_api_io. It is designed to terminate quickly and to
show the real SharpAPI HTTP state even when other providers are slow.
"""

import asyncio
import json
import os
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

import httpx

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
ARTIFACT_DIR = Path("artifacts/provider-smoke")
JSON_OUT = EXPORT_DIR / "latest-provider-smoke.json"
TXT_OUT = EXPORT_DIR / "latest-provider-smoke.txt"

SECRET_KEYS = ("SHARPAPI_API_KEY", "SHARPAPI_KEY", "SHARP_API_KEY")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def safe_float(value: object, default: float) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value))
    except Exception:
        return default


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return os.getenv("GITHUB_SHA", "")


def api_key() -> str:
    for key in SECRET_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def sanitize_preview(text: str, limit: int = 2500) -> str:
    value = str(text or "")[:limit]
    key = api_key()
    if key:
        value = value.replace(key, "***")
    return value


def payload_shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "list"
    if isinstance(payload, dict):
        return ",".join(sorted(map(str, payload.keys()))[:16])
    return type(payload).__name__


def count_rows(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    for key in ("data", "events", "matches", "fixtures", "games", "results", "odds"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            nested = count_rows(value)
            if nested:
                return nested
    return len(payload) if payload else 0


def verdict(status_code: int | None, error: str, rows: int, key_present: bool) -> tuple[str, str]:
    if not key_present:
        return "AUTH", "SHARPAPI_API_KEY/SHARPAPI_KEY/SHARP_API_KEY missing"
    if error:
        if "Timeout" in error or "timeout" in error.lower():
            return "TIMEOUT", error[:220]
        return "HTTP_ERROR", error[:220]
    if status_code in {401, 403}:
        return "AUTH", f"http_status={status_code}"
    if status_code == 429:
        return "RATE_LIMIT", "http_status=429"
    if status_code is None:
        return "HTTP_ERROR", "no HTTP response"
    if status_code < 200 or status_code >= 300:
        return "HTTP_ERROR", f"http_status={status_code}"
    if rows <= 0:
        return "EMPTY_FIXTURES", "HTTP 2xx but no event/odds rows found in JSON envelope"
    return "OK", f"rows={rows}"


async def probe() -> dict[str, Any]:
    key = api_key()
    base_url = str(os.getenv("SHARPAPI_BASE_URL") or "https://api.sharpapi.io").rstrip("/")
    endpoints = [item.strip() for item in str(os.getenv("SHARPAPI_ODDS_ENDPOINTS") or "/api/v1/odds").split(",") if item.strip()]
    endpoint = endpoints[0] if endpoints else "/api/v1/odds"
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    league = str(os.getenv("SHARPAPI_LEAGUE") or os.getenv("SHARPAPI_DEFAULT_LEAGUE") or "Soccer").strip()
    timeout_seconds = max(2.0, min(12.0, safe_float(os.getenv("SHARPAPI_TIMEOUT_SECONDS"), 6.0)))
    url = f"{base_url}{endpoint}"
    params = {
        "league": league,
        "sport": "soccer",
        "limit": str(os.getenv("SHARPAPI_MATCH_LIMIT") or "24"),
    }
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
        headers["Authorization"] = f"Bearer {key}"

    started = datetime.now(UTC)
    status_code: int | None = None
    text_preview = ""
    json_shape = ""
    rows = 0
    error = ""
    elapsed_ms = 0

    if key:
        try:
            timeout = httpx.Timeout(timeout_seconds, connect=min(4.0, timeout_seconds), read=timeout_seconds, write=4.0, pool=4.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            status_code = int(response.status_code)
            text_preview = sanitize_preview(response.text)
            try:
                payload = response.json()
                json_shape = payload_shape(payload)
                rows = count_rows(payload)
            except Exception as exc:
                json_shape = "invalid_json"
                error = f"json_parse:{type(exc).__name__}:{exc}"
        except Exception as exc:
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            error = f"{type(exc).__name__}: {exc}"

    status, reason = verdict(status_code, error, rows, bool(key))
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "github_sha": os.getenv("GITHUB_SHA", ""),
        "mode": "provider_smoke",
        "providers_requested": ["sharpapi"],
        "bootstrap": {
            "status": "SKIPPED",
            "matches_count": 0,
            "reason": "standalone_sharpapi_smoke_does_not_bootstrap_odds_api_io",
        },
        "summary": {
            "checks_ok": 1 if status == "OK" else 0,
            "checks_warning": 1 if status in {"EMPTY_FIXTURES", "HTTP_ERROR", "TIMEOUT"} else 0,
            "checks_error": 1 if status in {"AUTH", "RATE_LIMIT"} else 0,
            "providers_loaded": 1,
            "providers_total": 1,
        },
        "providers": {
            "sharpapi": {
                "provider": "sharpapi",
                "loaded": True,
                "secrets_present": {name: bool(os.getenv(name)) for name in SECRET_KEYS},
                "checks": {
                    "raw_endpoint": {
                        "status": status,
                        "reason": reason,
                        "data_count": rows,
                        "stats": {
                            "enabled": truthy(os.getenv("ENABLE_SHARPAPI", "true")),
                            "provider": "sharpapi",
                            "api_key_present": bool(key),
                            "base_url": base_url,
                            "endpoint": endpoint,
                            "url": url,
                            "params": params,
                            "requests": 1 if key else 0,
                            "http_statuses": {str(status_code): 1} if status_code is not None else {},
                            "status_code": status_code,
                            "elapsed_ms": elapsed_ms,
                            "payload_shape": json_shape,
                            "rows_detected": rows,
                            "last_body_preview": text_preview,
                            "error": error,
                            "timeout_seconds": timeout_seconds,
                        },
                        "preview": {
                            "body": text_preview,
                            "payload_shape": json_shape,
                        },
                    }
                },
            }
        },
        "settings": {
            "enable_sharpapi": os.getenv("ENABLE_SHARPAPI"),
            "sharpapi_base_url": base_url,
            "sharpapi_odds_endpoints": os.getenv("SHARPAPI_ODDS_ENDPOINTS"),
            "sharpapi_league": league,
            "sharpapi_timeout_seconds": timeout_seconds,
        },
    }
    return payload


def build_text(payload: dict[str, Any]) -> str:
    check = (((payload.get("providers") or {}).get("sharpapi") or {}).get("checks") or {}).get("raw_endpoint") or {}
    stats = check.get("stats") or {}
    lines = [
        "🧪 Provider smoke run",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• git: {str(payload.get('git_sha') or '')[:12]}",
        "• bootstrap: SKIPPED | standalone SharpAPI raw endpoint probe",
        f"• checks: OK {(payload.get('summary') or {}).get('checks_ok')} | warn {(payload.get('summary') or {}).get('checks_warning')} | errors {(payload.get('summary') or {}).get('checks_error')}",
        "",
        "📡 Providers",
        f"• sharpapi: raw_endpoint={check.get('status')} data={check.get('data_count')} ({check.get('reason')})",
        f"  base_url: {stats.get('base_url')}",
        f"  endpoint: {stats.get('endpoint')}",
        f"  api_key_present: {stats.get('api_key_present')}",
        f"  http_statuses: {json.dumps(stats.get('http_statuses') or {}, ensure_ascii=False)}",
        f"  elapsed_ms: {stats.get('elapsed_ms')}",
        f"  payload_shape: {stats.get('payload_shape')}",
        f"  rows_detected: {stats.get('rows_detected')}",
    ]
    if stats.get("error"):
        lines.append(f"  error: {stats.get('error')}")
    if stats.get("last_body_preview"):
        lines.append(f"  last_body_preview: {str(stats.get('last_body_preview'))[:1400]}")
    lines += ["", "📁 Files", str(JSON_OUT), str(TXT_OUT)]
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any]) -> None:
    text = build_text(payload)
    write_json(JSON_OUT, payload)
    write_text(TXT_OUT, text)
    write_json(ARTIFACT_DIR / "provider-smoke.json", payload)
    write_text(ARTIFACT_DIR / "provider-smoke.txt", text)


def send_telegram(text: str) -> bool:
    if not truthy(os.getenv("PROVIDER_SMOKE_SEND_TELEGRAM", "false")):
        return False
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        req = request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=parse.urlencode({"chat_id": chat_id, "text": text[:3600]}).encode("utf-8"),
        )
        with request.urlopen(req, timeout=15) as resp:  # nosec - CI diagnostic script
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def failure_payload(exc: BaseException) -> dict[str, Any]:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "mode": "provider_smoke",
        "fatal_error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc()[-5000:],
        "summary": {"checks_ok": 0, "checks_warning": 0, "checks_error": 1, "providers_loaded": 1, "providers_total": 1},
        "bootstrap": {"status": "SKIPPED", "matches_count": 0},
        "providers": {},
    }
    return payload


def main() -> int:
    try:
        payload = asyncio.run(probe())
    except BaseException as exc:
        payload = failure_payload(exc)
    write_outputs(payload)
    text = build_text(payload)
    print(text, flush=True)
    sent = send_telegram(text)
    payload["telegram_sent"] = sent
    write_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
