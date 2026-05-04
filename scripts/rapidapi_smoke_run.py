from __future__ import annotations

"""Standalone RapidAPI provider smoke wrapper.

Runs the existing RapidAPI quota probe and endpoint discovery in a bounded,
provider-smoke-friendly mode. This is diagnostic only and does not enable any
RapidAPI provider in the main prediction runtime.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
ARTIFACT_DIR = Path("artifacts/provider-smoke")
PROVIDER_SMOKE_JSON = EXPORT_DIR / "latest-provider-smoke.json"
PROVIDER_SMOKE_TXT = EXPORT_DIR / "latest-provider-smoke.txt"
QUOTA_JSON = EXPORT_DIR / "latest-rapidapi-provider-probe.json"
QUOTA_SUMMARY_JSON = EXPORT_DIR / "latest-rapidapi-provider-summary.json"
DISCOVERY_JSON = EXPORT_DIR / "latest-rapidapi-endpoint-discovery.json"
DISCOVERY_SUMMARY_JSON = EXPORT_DIR / "latest-rapidapi-endpoint-discovery-summary.json"
STATE_PATH = Path(".data/provider_quota_state.json")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
    except Exception:
        return os.getenv("GITHUB_SHA", "")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


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


def smoke_env() -> dict[str, str]:
    env = dict(os.environ)
    overrides = {
        "RAPIDAPI_PROBE_ENABLED": "true",
        "RAPIDAPI_ENDPOINT_DISCOVERY_ENABLED": "true",
        "RAPIDAPI_PROBE_TIMEOUT_SECONDS": os.getenv("RAPIDAPI_SMOKE_TIMEOUT_SECONDS") or "8",
        "RAPIDAPI_ENDPOINT_DISCOVERY_TIMEOUT_SECONDS": os.getenv("RAPIDAPI_SMOKE_TIMEOUT_SECONDS") or "8",
        "RAPIDAPI_DEFAULT_DAILY_LIMIT": "1",
        "RAPIDAPI_SPORTSBOOK_PROBE_ENABLED": "true",
        "RAPIDAPI_ODDS_FEED_PROBE_ENABLED": "true",
        "RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED": "true",
        "RAPIDAPI_SPORTAPI7_PROBE_ENABLED": "true",
        "RAPIDAPI_SPORTSBOOK_DAILY_LIMIT": "1",
        "RAPIDAPI_ODDS_FEED_DAILY_LIMIT": "1",
        "RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT": "1",
        "RAPIDAPI_SPORTAPI7_DAILY_LIMIT": "1",
        "RAPIDAPI_DISCOVERY_MAX_CALLS_TOTAL": os.getenv("RAPIDAPI_SMOKE_DISCOVERY_MAX_CALLS_TOTAL") or "10",
        "RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS": "2",
        "RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS": "2",
        "RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS": "3",
        "RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS": "3",
        "PROVIDER_SMOKE_SEND_TELEGRAM": os.getenv("PROVIDER_SMOKE_SEND_TELEGRAM") or "false",
    }
    env.update(overrides)
    return env


def run_script(path: str, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    started = datetime.now(UTC)
    result: dict[str, Any] = {"script": path, "started_at_utc": started.isoformat(), "returncode": None, "timeout": False, "stdout_tail": "", "stderr_tail": ""}
    try:
        proc = subprocess.run(
            [sys.executable, "-u", path],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        result["returncode"] = proc.returncode
        result["stdout_tail"] = (proc.stdout or "")[-4000:]
        result["stderr_tail"] = (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired as exc:
        result["returncode"] = 124
        result["timeout"] = True
        result["stdout_tail"] = str(exc.stdout or "")[-4000:]
        result["stderr_tail"] = str(exc.stderr or "")[-2000:]
    result["elapsed_ms"] = int((datetime.now(UTC) - started).total_seconds() * 1000)
    return result


def provider_status_from_quota(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in summary.get("results") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "provider": item.get("provider"),
            "called": item.get("called"),
            "ok": item.get("ok"),
            "status_code": item.get("status_code"),
            "skip_reason": item.get("skip_reason"),
            "auth_failed": bool(item.get("auth_failed")),
            "rate_limited": bool(item.get("rate_limited")),
            "shape": item.get("shape"),
        })
    return rows


def build_payload(script_results: list[dict[str, Any]]) -> dict[str, Any]:
    quota = load_json(QUOTA_JSON, {})
    quota_summary = load_json(QUOTA_SUMMARY_JSON, {})
    discovery = load_json(DISCOVERY_SUMMARY_JSON, {})
    checks_error = 0
    checks_warning = 0
    checks_ok = 0
    provider_rows = provider_status_from_quota(quota)
    for row in provider_rows:
        if row.get("ok"):
            checks_ok += 1
        elif row.get("auth_failed") or row.get("rate_limited"):
            checks_error += 1
        else:
            checks_warning += 1
    if not provider_rows:
        checks_warning += 1
    useful = int(discovery.get("useful_football_like") or 0) if isinstance(discovery, dict) else 0
    if useful:
        checks_ok += 1
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
                "scripts": script_results,
                "quota_probe": quota,
                "quota_summary": quota_summary,
                "endpoint_discovery_summary": discovery,
                "provider_status": provider_rows,
            }
        },
        "files": {
            "quota_probe": str(QUOTA_JSON),
            "quota_summary": str(QUOTA_SUMMARY_JSON),
            "endpoint_discovery": str(DISCOVERY_JSON),
            "endpoint_discovery_summary": str(DISCOVERY_SUMMARY_JSON),
        },
    }
    return payload


def build_text(payload: dict[str, Any]) -> str:
    info = ((payload.get("providers") or {}).get("rapidapi") or {})
    summary = payload.get("summary") or {}
    lines = [
        "🧪 Provider smoke run",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• git: {str(payload.get('git_sha') or '')[:12]}",
        "• bootstrap: SKIPPED | standalone RapidAPI probe",
        f"• checks: OK {summary.get('checks_ok')} | warn {summary.get('checks_warning')} | errors {summary.get('checks_error')}",
        "",
        "📡 RapidAPI secrets",
    ]
    for key, present in (info.get("secrets_present") or {}).items():
        lines.append(f"• {key}: {'yes' if present else 'no'}")
    lines.append("")
    lines.append("📡 RapidAPI providers")
    rows = info.get("provider_status") or []
    if not rows:
        lines.append("• no provider rows from quota probe")
    for row in rows:
        lines.append(
            f"• {row.get('provider')}: called={row.get('called')} ok={row.get('ok')} "
            f"status={row.get('status_code')} skip={row.get('skip_reason')} "
            f"auth={row.get('auth_failed')} rate_limit={row.get('rate_limited')}"
        )
        if row.get("shape"):
            lines.append(f"  shape: {json.dumps(row.get('shape'), ensure_ascii=False)[:700]}")
    discovery = info.get("endpoint_discovery_summary") or {}
    lines += [
        "",
        "🔎 Endpoint discovery",
        f"• called: {discovery.get('called')} | ok: {discovery.get('ok')} | useful football-like: {discovery.get('useful_football_like')} | errors: {discovery.get('errors')} | skipped: {discovery.get('skipped')}",
    ]
    for item in (discovery.get("top_useful_endpoints") or [])[:8]:
        lines.append(f"• useful: {item.get('provider')} / {item.get('label')} / status={item.get('status_code')}")
        lines.append(f"  url: {item.get('url')}")
        lines.append(f"  flags: {', '.join(item.get('useful_flags') or [])}")
    for item in (discovery.get("error_endpoints") or [])[:8]:
        lines.append(f"• error: {item.get('provider')} / {item.get('label')} / status={item.get('status_code')} skip={item.get('skip_reason')}")
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
        with request.urlopen(req, timeout=15) as resp:  # nosec - CI diagnostic script
            return 200 <= int(getattr(resp, "status", 0) or 0) < 300
    except Exception:
        return False


def main() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    if truthy(os.getenv("RAPIDAPI_SMOKE_RESET_STATE", "true")):
        try:
            STATE_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    env = smoke_env()
    script_results = [
        run_script("scripts/rapidapi_quota_probe.py", env, int(os.getenv("RAPIDAPI_SMOKE_QUOTA_TIMEOUT_SECONDS") or "45")),
        run_script("scripts/rapidapi_endpoint_discovery.py", env, int(os.getenv("RAPIDAPI_SMOKE_DISCOVERY_TIMEOUT_SECONDS") or "90")),
    ]
    payload = build_payload(script_results)
    text = build_text(payload)
    print(text, flush=True)
    payload["telegram_sent"] = send_telegram(text)
    write_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
