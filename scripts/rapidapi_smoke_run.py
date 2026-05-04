from __future__ import annotations

"""Standalone RapidAPI provider smoke probe.

Pure-stdlib, no app imports, no httpx, no subprocess child scripts. The workflow
runs this with `python -S` so sitecustomize/usercustomize hooks cannot block the
probe before diagnostics are printed.
"""

import json
import os
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
ARTIFACT_DIR = Path("artifacts/provider-smoke")
PROVIDER_SMOKE_JSON = EXPORT_DIR / "latest-provider-smoke.json"
PROVIDER_SMOKE_TXT = EXPORT_DIR / "latest-provider-smoke.txt"
DISCOVERY_JSON = EXPORT_DIR / "latest-rapidapi-endpoint-discovery.json"
DISCOVERY_SUMMARY_JSON = EXPORT_DIR / "latest-rapidapi-endpoint-discovery-summary.json"
QUOTA_JSON = EXPORT_DIR / "latest-rapidapi-provider-probe.json"
QUOTA_SUMMARY_JSON = EXPORT_DIR / "latest-rapidapi-provider-summary.json"


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
    except Exception:
        return os.getenv("GITHUB_SHA", "")


def secret_presence() -> dict[str, bool]:
    keys = [
        "RAPIDAPI_KEY",
        "SPORTSBOOK_RAPIDAPI_KEY",
        "ODDS_FEED_RAPIDAPI_KEY",
        "FREE_FOOTBALL_RAPIDAPI_KEY",
        "SPORTAPI7_RAPIDAPI_KEY",
        "METEOSTAT_RAPIDAPI_KEY",
        "ODDSPAPI_API_KEY",
        "ALLSPORTSAPI_API_KEY",
    ]
    return {key: bool(str(os.getenv(key) or "").strip()) for key in keys}


def rapidapi_key(key_env: str | None) -> str:
    for name in [key_env, "RAPIDAPI_KEY", "X_RAPIDAPI_KEY"]:
        if not name:
            continue
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def safe_preview(text: str, limit: int = 1200) -> str:
    return str(text or "")[:limit]


def shape_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        result: dict[str, Any] = {"type": "dict", "keys": list(payload.keys())[:30]}
        for key in ("data", "response", "results", "events", "matches", "fixtures", "leagues", "sports", "markets", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                result[f"{key}_len"] = len(value)
                if value and isinstance(value[0], dict):
                    result[f"{key}_first_keys"] = list(value[0].keys())[:20]
            elif isinstance(value, dict):
                result[f"{key}_keys"] = list(value.keys())[:20]
        return result
    if isinstance(payload, list):
        result = {"type": "list", "len": len(payload)}
        if payload and isinstance(payload[0], dict):
            result["first_keys"] = list(payload[0].keys())[:20]
        return result
    return {"type": type(payload).__name__}


def looks_useful_for_football(payload: Any) -> tuple[bool, list[str]]:
    try:
        text = json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        text = str(payload).lower()
    flags = []
    for token in ("football", "soccer", "fixture", "fixtures", "match", "matches", "event", "events", "team", "league", "tournament", "odds", "market", "bookmaker"):
        if token in text:
            flags.append(token)
    return bool(flags), sorted(set(flags))


def fetch_json(url: str, host: str, key_env: str | None, timeout_seconds: float = 6.0) -> dict[str, Any]:
    key = rapidapi_key(key_env)
    started = datetime.now(UTC)
    status_code: int | None = None
    body = ""
    payload: Any = None
    error = ""
    if not key:
        return {
            "called": False,
            "skip_reason": "missing_rapidapi_key",
            "status_code": None,
            "ok": False,
            "error": "missing_rapidapi_key",
            "elapsed_ms": 0,
            "shape": {},
            "preview": "",
            "useful": False,
            "flags": [],
        }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "harizon-rapidapi-smoke/1.0",
        "x-rapidapi-host": host,
        "x-rapidapi-key": key,
    }
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec - CI diagnostic script
            status_code = int(getattr(resp, "status", 0) or 0)
            body = resp.read(700_000).decode("utf-8", errors="replace")
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
        except Exception:
            payload = body
    shape = shape_summary(payload)
    useful, flags = looks_useful_for_football(payload)
    return {
        "called": True,
        "skip_reason": None,
        "status_code": status_code,
        "ok": bool(status_code is not None and 200 <= int(status_code) < 300),
        "auth_failed": status_code in {401, 403},
        "rate_limited": status_code == 429,
        "not_found": status_code == 404,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "shape": shape,
        "preview": safe_preview(body),
        "useful": useful,
        "flags": flags,
    }


def endpoint_candidates() -> list[dict[str, str]]:
    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)
    return [
        {
            "provider": "sportsbook_api",
            "label": "sports list",
            "host": "sportsbook-api2.p.rapidapi.com",
            "key_env": "SPORTSBOOK_RAPIDAPI_KEY",
            "url": "https://sportsbook-api2.p.rapidapi.com/v0/sports",
            "expected_use": "sports list",
        },
        {
            "provider": "sportsbook_api",
            "label": "soccer events",
            "host": "sportsbook-api2.p.rapidapi.com",
            "key_env": "SPORTSBOOK_RAPIDAPI_KEY",
            "url": "https://sportsbook-api2.p.rapidapi.com/v0/events?sport=SOCCER",
            "expected_use": "soccer events",
        },
        {
            "provider": "sportsbook_api",
            "label": "arbitrage control",
            "host": "sportsbook-api2.p.rapidapi.com",
            "key_env": "SPORTSBOOK_RAPIDAPI_KEY",
            "url": "https://sportsbook-api2.p.rapidapi.com/v0/advantages/?type=ARBITRAGE",
            "expected_use": "advantages/arbitrage control",
        },
        {
            "provider": "odds_feed",
            "label": "sports",
            "host": "odds-feed.p.rapidapi.com",
            "key_env": "ODDS_FEED_RAPIDAPI_KEY",
            "url": "https://odds-feed.p.rapidapi.com/api/v1/sports",
            "expected_use": "sports list",
        },
        {
            "provider": "odds_feed",
            "label": "events",
            "host": "odds-feed.p.rapidapi.com",
            "key_env": "ODDS_FEED_RAPIDAPI_KEY",
            "url": "https://odds-feed.p.rapidapi.com/api/v1/events?sport=soccer",
            "expected_use": "soccer event list",
        },
        {
            "provider": "free_live_football_data",
            "label": "player search control",
            "host": "free-api-live-football-data.p.rapidapi.com",
            "key_env": "FREE_FOOTBALL_RAPIDAPI_KEY",
            "url": "https://free-api-live-football-data.p.rapidapi.com/football-players-search?search=m",
            "expected_use": "known working/schema control",
        },
        {
            "provider": "free_live_football_data",
            "label": "matches today",
            "host": "free-api-live-football-data.p.rapidapi.com",
            "key_env": "FREE_FOOTBALL_RAPIDAPI_KEY",
            "url": "https://free-api-live-football-data.p.rapidapi.com/football-matches-today",
            "expected_use": "today fixtures/matches",
        },
        {
            "provider": "free_live_football_data",
            "label": "live matches",
            "host": "free-api-live-football-data.p.rapidapi.com",
            "key_env": "FREE_FOOTBALL_RAPIDAPI_KEY",
            "url": "https://free-api-live-football-data.p.rapidapi.com/football-live-matches",
            "expected_use": "live matches",
        },
        {
            "provider": "sportapi7",
            "label": "football scheduled today",
            "host": "sportapi7.p.rapidapi.com",
            "key_env": "SPORTAPI7_RAPIDAPI_KEY",
            "url": f"https://sportapi7.p.rapidapi.com/api/v1/sport/football/scheduled-events/{today.isoformat()}",
            "expected_use": "football scheduled events",
        },
        {
            "provider": "sportapi7",
            "label": "football scheduled tomorrow",
            "host": "sportapi7.p.rapidapi.com",
            "key_env": "SPORTAPI7_RAPIDAPI_KEY",
            "url": f"https://sportapi7.p.rapidapi.com/api/v1/sport/football/scheduled-events/{tomorrow.isoformat()}",
            "expected_use": "football scheduled events",
        },
    ]


def run_probe() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timeout_seconds = max(2.0, min(8.0, float(os.getenv("RAPIDAPI_SMOKE_TIMEOUT_SECONDS") or "5")))
    max_calls = max(1, min(12, int(float(os.getenv("RAPIDAPI_SMOKE_DISCOVERY_MAX_CALLS_TOTAL") or "10"))))
    rows = []
    print(f"[rapidapi-smoke] starting pure-stdlib probe max_calls={max_calls} timeout={timeout_seconds}s", flush=True)
    for index, candidate in enumerate(endpoint_candidates()[:max_calls], start=1):
        print(f"[rapidapi-smoke] {index}/{max_calls} {candidate['provider']} :: {candidate['label']}", flush=True)
        result = fetch_json(candidate["url"], candidate["host"], candidate.get("key_env"), timeout_seconds)
        row = dict(candidate)
        row.update(result)
        rows.append(row)
    provider_summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = str(row.get("provider") or "unknown")
        item = provider_summary.setdefault(provider, {"provider": provider, "called": 0, "ok": 0, "auth_failed": 0, "rate_limited": 0, "not_found": 0, "useful": 0, "statuses": {}})
        if row.get("called"):
            item["called"] += 1
        if row.get("ok"):
            item["ok"] += 1
        if row.get("auth_failed"):
            item["auth_failed"] += 1
        if row.get("rate_limited"):
            item["rate_limited"] += 1
        if row.get("not_found"):
            item["not_found"] += 1
        if row.get("useful"):
            item["useful"] += 1
        status = str(row.get("status_code"))
        item["statuses"][status] = int(item["statuses"].get(status) or 0) + 1
    return rows, list(provider_summary.values())


def build_payload(rows: list[dict[str, Any]], provider_rows: list[dict[str, Any]]) -> dict[str, Any]:
    useful = [row for row in rows if row.get("ok") and row.get("useful")]
    errors = [row for row in rows if row.get("called") and not row.get("ok")]
    auth_errors = [row for row in rows if row.get("auth_failed")]
    rate_limits = [row for row in rows if row.get("rate_limited")]
    checks_ok = len(useful)
    checks_error = len(auth_errors) + len(rate_limits)
    checks_warning = max(0, len(rows) - checks_ok - checks_error)
    discovery_summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "candidates_total": len(rows),
        "called": sum(1 for row in rows if row.get("called")),
        "ok": sum(1 for row in rows if row.get("ok")),
        "useful_football_like": len(useful),
        "errors": len(errors),
        "skipped": sum(1 for row in rows if not row.get("called")),
        "top_useful_endpoints": [
            {
                "provider": row.get("provider"),
                "label": row.get("label"),
                "url": row.get("url"),
                "status_code": row.get("status_code"),
                "shape": row.get("shape"),
                "useful_flags": row.get("flags"),
            }
            for row in useful[:20]
        ],
        "ok_endpoints": [
            {"provider": row.get("provider"), "label": row.get("label"), "url": row.get("url"), "shape": row.get("shape")}
            for row in rows if row.get("ok")
        ],
        "error_endpoints": [
            {
                "provider": row.get("provider"),
                "label": row.get("label"),
                "url": row.get("url"),
                "status_code": row.get("status_code"),
                "error": row.get("error"),
                "shape": row.get("shape"),
            }
            for row in errors[:30]
        ],
    }
    quota_summary = {
        "created_at": discovery_summary["created_at"],
        "enabled": True,
        "providers_total": len(provider_rows),
        "called": sum(1 for row in provider_rows if row.get("called")),
        "ok": sum(1 for row in provider_rows if row.get("ok")),
        "rate_limited": sum(1 for row in provider_rows if row.get("rate_limited")),
        "auth_failed": sum(1 for row in provider_rows if row.get("auth_failed")),
        "provider_status": provider_rows,
    }
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "github_sha": os.getenv("GITHUB_SHA", ""),
        "mode": "provider_smoke",
        "providers_requested": ["rapidapi"],
        "bootstrap": {"status": "SKIPPED", "matches_count": 0, "reason": "standalone_rapidapi_smoke_does_not_bootstrap_matches"},
        "summary": {
            "checks_ok": checks_ok,
            "checks_warning": checks_warning,
            "checks_error": checks_error,
            "providers_loaded": len(provider_rows),
            "providers_total": max(len(provider_rows), 4),
        },
        "providers": {
            "rapidapi": {
                "provider": "rapidapi",
                "loaded": True,
                "secrets_present": secret_presence(),
                "provider_status": provider_rows,
                "endpoint_rows": rows,
                "endpoint_discovery_summary": discovery_summary,
                "quota_summary": quota_summary,
            }
        },
        "files": {
            "quota_probe": str(QUOTA_JSON),
            "quota_summary": str(QUOTA_SUMMARY_JSON),
            "endpoint_discovery": str(DISCOVERY_JSON),
            "endpoint_discovery_summary": str(DISCOVERY_SUMMARY_JSON),
        },
    }
    write_json(DISCOVERY_JSON, {"created_at": discovery_summary["created_at"], "results": rows})
    write_json(DISCOVERY_SUMMARY_JSON, discovery_summary)
    write_json(QUOTA_JSON, {"created_at": discovery_summary["created_at"], "results": rows})
    write_json(QUOTA_SUMMARY_JSON, quota_summary)
    return payload


def build_text(payload: dict[str, Any]) -> str:
    info = ((payload.get("providers") or {}).get("rapidapi") or {})
    summary = payload.get("summary") or {}
    lines = [
        "🧪 Provider smoke run",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• git: {str(payload.get('git_sha') or '')[:12]}",
        "• bootstrap: SKIPPED | standalone RapidAPI pure-stdlib probe",
        f"• checks: OK {summary.get('checks_ok')} | warn {summary.get('checks_warning')} | errors {summary.get('checks_error')}",
        "",
        "📡 RapidAPI secrets",
    ]
    for key, present in (info.get("secrets_present") or {}).items():
        lines.append(f"• {key}: {'yes' if present else 'no'}")
    lines += ["", "📡 RapidAPI providers"]
    for row in info.get("provider_status") or []:
        lines.append(
            f"• {row.get('provider')}: called={row.get('called')} ok={row.get('ok')} useful={row.get('useful')} "
            f"auth={row.get('auth_failed')} rate_limit={row.get('rate_limited')} statuses={json.dumps(row.get('statuses') or {}, ensure_ascii=False)}"
        )
    lines += ["", "🔎 Endpoint discovery"]
    discovery = info.get("endpoint_discovery_summary") or {}
    lines.append(
        f"• called: {discovery.get('called')} | ok: {discovery.get('ok')} | useful football-like: {discovery.get('useful_football_like')} | errors: {discovery.get('errors')} | skipped: {discovery.get('skipped')}"
    )
    for row in (info.get("endpoint_rows") or [])[:12]:
        lines.append(
            f"• {row.get('provider')} / {row.get('label')}: ok={row.get('ok')} status={row.get('status_code')} useful={row.get('useful')} flags={','.join(row.get('flags') or [])} elapsed={row.get('elapsed_ms')}ms"
        )
        if row.get("shape"):
            lines.append(f"  shape: {json.dumps(row.get('shape'), ensure_ascii=False)[:700]}")
        if row.get("error"):
            lines.append(f"  error: {row.get('error')}")
        if row.get("preview"):
            lines.append(f"  preview: {str(row.get('preview'))[:700]}")
    lines += ["", "📁 Files"]
    for value in (payload.get("files") or {}).values():
        lines.append(str(value))
    lines.append(str(PROVIDER_SMOKE_JSON))
    lines.append(str(PROVIDER_SMOKE_TXT))
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any]) -> None:
    text = build_text(payload)
    write_json(PROVIDER_SMOKE_JSON, payload)
    write_text(PROVIDER_SMOKE_TXT, text)
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
        with request.urlopen(req, timeout=10) as resp:  # nosec - CI diagnostic script
            return 200 <= int(getattr(resp, "status", 0) or 0) < 300
    except Exception:
        return False


def main() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    rows, provider_rows = run_probe()
    payload = build_payload(rows, provider_rows)
    text = build_text(payload)
    print(text, flush=True)
    payload["telegram_sent"] = send_telegram(text)
    write_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
