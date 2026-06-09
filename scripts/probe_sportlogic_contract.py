from __future__ import annotations

"""Small, safe SportLogic contract probe.

This is diagnostics only. It does not publish picks, does not alter inventory, and
never logs API secrets. It exists because the main provider reports SportLogic
requests but the previous runtime diagnostic had zero captured HTTP attempts.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
OUT = EXPORT_DIR / "latest-sportlogic-api-diagnostic.json"


def _truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _safe_text(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    for secret_name in ("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"):
        secret = os.getenv(secret_name)
        if secret:
            text = text.replace(secret, "***")
    return text[:limit]


def _rows_count(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("results", "data", "events", "fixtures", "matches", "items", "response", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                nested = _rows_count(value)
                if nested:
                    return nested
    return 0


def _redact_params(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        low = str(key).lower()
        out[str(key)] = "***" if any(t in low for t in ("key", "token", "secret", "auth")) else str(value)[:120]
    return out


def _headers(mode: str, key: str) -> dict[str, str]:
    base = {"Accept": "application/json", "User-Agent": "HARIZON-SportLogic-Probe/1.0"}
    if not key:
        return base
    if mode == "x-api-key":
        base["X-API-Key"] = key
    elif mode == "apikey":
        base["apikey"] = key
    elif mode == "api-key":
        base["api-key"] = key
    elif mode == "bearer":
        base["Authorization"] = f"Bearer {key}"
    elif mode == "token":
        base["Authorization"] = f"Token {key}"
    return base


def _base_urls() -> list[str]:
    candidates = [
        os.getenv("SPORTLOGIC_BASE_URL"),
        "https://api.sportlogic.io/api/v1",
        "https://api.sportlogic.io/v1",
        "https://api.sportlogic.io",
    ]
    out: list[str] = []
    for item in candidates:
        text = str(item or "").strip().rstrip("/")
        if text and text not in out:
            out.append(text)
    return out


def _paths() -> list[str]:
    raw = os.getenv("SPORTLOGIC_CONTRACT_PROBE_PATHS")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return ["/games", "/fixtures", "/events", "/matches", "/soccer/games", "/football/games"]


def _date_param_variants() -> list[dict[str, Any]]:
    now = datetime.now(UTC).date()
    tomorrow = now + timedelta(days=1)
    date = now.isoformat()
    next_date = tomorrow.isoformat()
    per_page = max(20, min(200, _as_int(os.getenv("SPORTLOGIC_CONTRACT_PROBE_PER_PAGE"), 100)))
    return [
        {"date_from": date, "date_to": next_date, "status": "scheduled", "per_page": per_page},
        {"date_from": date, "date_to": next_date, "per_page": per_page},
        {"date": date, "status": "scheduled", "per_page": per_page},
        {"date": date, "per_page": per_page},
        {"from": date, "to": next_date, "status": "scheduled", "per_page": per_page},
        {"start_date": date, "end_date": next_date, "status": "scheduled", "per_page": per_page},
    ]


def run_probe() -> dict[str, Any]:
    key = os.getenv("SPORTLOGIC_API_KEY") or os.getenv("SPORTLOGIC_KEY") or os.getenv("SPORTLOGIC_TOKEN") or ""
    if not _truthy(os.getenv("SPORTLOGIC_CONTRACT_PROBE_ENABLED", "true"), True):
        payload = {"status": "disabled", "created_at_utc": datetime.now(UTC).isoformat()}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload

    timeout = float(os.getenv("SPORTLOGIC_CONTRACT_PROBE_TIMEOUT_SECONDS") or "8")
    max_attempts = max(1, _as_int(os.getenv("SPORTLOGIC_CONTRACT_PROBE_MAX_ATTEMPTS"), 18))
    auth_modes = [m.strip() for m in (os.getenv("SPORTLOGIC_CONTRACT_PROBE_AUTH_MODES") or "x-api-key,bearer,token,apikey,api-key").split(",") if m.strip()]
    if not key:
        auth_modes = ["none"]

    attempts: list[dict[str, Any]] = []
    rows_total = 0
    found_rows = False

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for base_url in _base_urls():
            for path in _paths():
                for params in _date_param_variants():
                    for auth_mode in auth_modes:
                        if len(attempts) >= max_attempts or found_rows:
                            break
                        url = f"{base_url}{path}"
                        attempt: dict[str, Any] = {
                            "attempt_no": len(attempts) + 1,
                            "base_url_hint": base_url.replace(key, "***") if key else base_url,
                            "path": path,
                            "params": _redact_params(params),
                            "auth_mode": auth_mode,
                            "rows_count": 0,
                            "status_code": 0,
                        }
                        try:
                            response = client.get(url, headers=_headers(auth_mode, key), params=params)
                            text = response.text or ""
                            attempt["status_code"] = int(response.status_code)
                            attempt["content_type"] = response.headers.get("content-type", "")[:120]
                            attempt["body_preview"] = _safe_text(text)
                            try:
                                parsed = response.json()
                                rows = _rows_count(parsed)
                                attempt["payload_type"] = type(parsed).__name__
                                attempt["rows_count"] = rows
                                rows_total += rows
                                if rows > 0:
                                    found_rows = True
                            except Exception:
                                attempt["payload_type"] = "text"
                        except Exception as exc:
                            attempt["error"] = _safe_text(exc, 240)
                        attempts.append(attempt)
                    if len(attempts) >= max_attempts or found_rows:
                        break
                if len(attempts) >= max_attempts or found_rows:
                    break
            if len(attempts) >= max_attempts or found_rows:
                break

    status_counts: dict[str, int] = {}
    for item in attempts:
        status_counts[str(item.get("status_code"))] = status_counts.get(str(item.get("status_code")), 0) + 1
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "probe": "sportlogic_contract_probe_v1",
        "has_api_key": bool(key),
        "attempts_count": len(attempts),
        "rows_total": rows_total,
        "found_rows": found_rows,
        "status_counts": status_counts,
        "attempts_tail": attempts[-20:],
        "next_hint": "If all status codes are 401/403, fix secret/auth mode; if 404, fix base URL/path; if 200 with rows=0, fix date/status params or SportLogic plan coverage.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main(argv: list[str] | None = None) -> int:
    run_probe()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
