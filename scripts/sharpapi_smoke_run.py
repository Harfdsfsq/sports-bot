from __future__ import annotations

"""Standalone SharpAPI smoke probe.

Pure-stdlib, no app imports, no httpx, no PredictionRunner. The workflow runs
this with `python -S` so repository sitecustomize/usercustomize hooks cannot
block the smoke before the HTTP probe starts.
"""

import json
import os
import socket
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

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
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
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
        low = error.lower()
        if "timed out" in low or "timeout" in low:
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


def fetch_url(url: str, params: dict[str, str], headers: dict[str, str], timeout_seconds: float) -> tuple[int | None, str, str, int, str]:
    full_url = f"{url}?{parse.urlencode(params)}"
    req = request.Request(full_url, headers=headers, method="GET")
    started = datetime.now(UTC)
    status_code: int | None = None
    body = ""
    json_shape = ""
    rows = 0
    error = ""
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec - diagnostic smoke script
            status_code = int(getattr(resp, "status", 0) or 0)
            raw = resp.read(700_000)
            body = raw.decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        status_code = int(getattr(exc, "code", 0) or 0)
        try:
            body = exc.read(700_000).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
    except (urlerror.URLError, TimeoutError, socket.timeout, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    if body:
        try:
            payload = json.loads(body)
            json_shape = payload_shape(payload)
            rows = count_rows(payload)
        except Exception as exc:
            json_shape = "invalid_json"
            if not error:
                error = f"json_parse:{type(exc).__name__}:{exc}"
    return status_code, body, json_shape, rows, error or f"elapsed_ms={elapsed_ms}"


def probe() -> dict[str, Any]:
    key = api_key()
    base_url = str(os.getenv("SHARPAPI_BASE_URL") or "https://api.sharpapi.io").rstrip("/")
    endpoints = [item.strip() for item in str(os.getenv("SHARPAPI_ODDS_ENDPOINTS") or "/api/v1/odds").split(",") if item.strip()]
    endpoint = endpoints[0] if endpoints else "/api/v1/odds"
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    league = str(os.getenv("SHARPAPI_LEAGUE") or os.getenv("SHARPAPI_DEFAULT_LEAGUE") or "Soccer").strip()
    timeout_seconds = max(2.0, min(8.0, safe_float(os.getenv("SHARPAPI_TIMEOUT_SECONDS"), 5.0)))
    url = f"{base_url}{endpoint}"
    params = {
        "league": league,
        "sport": "soccer",
        "limit": str(os.getenv("SHARPAPI_MATCH_LIMIT") or "24"),
    }
    headers = {"Accept": "application/json", "User-Agent": "harizon-provider-smoke/1.0"}
    if key:
        headers["X-API-Key"] = key
        headers["Authorization"] = f"Bearer {key}"

    print(f"[sharpapi-smoke] probing {url} league={league} timeout={timeout_seconds}s key_present={bool(key)}", flush=True)
    started = datetime.now(UTC)
    status_code: int | None = None
    body = ""
    json_shape = ""
    rows = 0
    error = ""
    if key:
        status_code, body, json_shape, rows, raw_error = fetch_url(url, params, headers, timeout_seconds)
        if not str(raw_error).startswith("elapsed_ms="):
            error = raw_error
    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)

    status, reason = verdict(status_code, error, rows, bool(key))
    text_preview = sanitize_preview(body)
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
                        "preview": {"body": text_preview, "payload_shape": json_shape},
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
            method="POST",
        )
        with request.urlopen(req, timeout=5) as resp:  # nosec - CI diagnostic script
            return 200 <= int(getattr(resp, "status", 0) or 0) < 300
    except Exception:
        return False


def failure_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "mode": "provider_smoke",
        "fatal_error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc()[-5000:],
        "summary": {"checks_ok": 0, "checks_warning": 0, "checks_error": 1, "providers_loaded": 1, "providers_total": 1},
        "bootstrap": {"status": "SKIPPED", "matches_count": 0},
        "providers": {},
    }


def main() -> int:
    try:
        payload = probe()
    except BaseException as exc:
        payload = failure_payload(exc)
    write_outputs(payload)
    text = build_text(payload)
    print(text, flush=True)
    payload["telegram_sent"] = send_telegram(text)
    write_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
