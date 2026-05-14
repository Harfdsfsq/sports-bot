from __future__ import annotations

"""Run a bounded API health probe after provider policy is applied.

The normal run report shows providers that participated in prediction, but some
configured APIs are rotation/probe/optional sources. This guard runs the existing
quick health probe so reports can show whether those APIs are configured,
reachable, disabled by policy, or failing.

If the SportLogic daily circuit is open, the subprocess health probe receives a
masked SportLogic environment so it cannot spend another HTTP request while the
free daily quota is exhausted. After the probe, this module rewrites the
SportLogic row to `skipped_daily_circuit` so the report does not show a false
critical failure.
"""

import atexit
import json
import os
import subprocess
import sys
from pathlib import Path

PATCH_MARKER = "_harizon_api_health_runtime_guard_v3"
OUT_DIR = Path(".data/exports")
HEALTH_JSON = OUT_DIR / "latest-api-health-run.json"
HEALTH_MD = OUT_DIR / "latest-api-health-run.md"
SPORTLOGIC_MARKERS = [
    Path(".data/cache/sportlogic_daily_limit_open.json"),
    Path(".data/line_history/sportlogic_daily_limit_open.json"),
    Path(".data/cache/sportlogic_daily_circuit.json"),
    Path(".data/line_history/sportlogic_daily_circuit.json"),
]


def _is_provider_budget_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else "").replace("\\", "/")
    return argv0.endswith("scripts/apply_provider_request_budget.py") or argv0.endswith("apply_provider_request_budget.py")


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _utc_day() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _sportlogic_circuit_open() -> bool:
    if _truthy(os.getenv("SPORTLOGIC_DAILY_CIRCUIT_OPEN")):
        return True
    today = _utc_day()
    for path in SPORTLOGIC_MARKERS:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("status") == "open" and payload.get("date_utc") == today:
            return True
    return False


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    if _sportlogic_circuit_open():
        env.update({
            "SPORTLOGIC_DAILY_CIRCUIT_OPEN": "true",
            "SPORTLOGIC_DAILY_CIRCUIT_REASON": env.get("SPORTLOGIC_DAILY_CIRCUIT_REASON", "api_health_guard_masked"),
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
            # The health script only checks key presence before calling the endpoint,
            # so blank the aliases for this subprocess when the daily circuit is open.
            "SPORTLOGIC_API_KEY": "",
            "SPORTLOGIC_KEY": "",
            "SPORTLOGIC_TOKEN": "",
        })
    return env


def _recount(report: dict) -> None:
    rows = [row for row in report.get("results", []) if isinstance(row, dict)]
    non_failure = {"ok", "degraded", "config_only", "skipped_daily_circuit"}
    by_status: dict[str, int] = {}
    by_group: dict[str, dict[str, int]] = {}
    critical_failures = []
    for row in rows:
        status = str(row.get("status") or "")
        group = str(row.get("group") or "")
        by_status[status] = by_status.get(status, 0) + 1
        by_group.setdefault(group, {})[status] = by_group.setdefault(group, {}).get(status, 0) + 1
        if row.get("critical") and status not in non_failure:
            critical_failures.append(row)
    summary = dict(report.get("summary") or {})
    summary.update({
        "providers_checked": len(rows),
        "ok": by_status.get("ok", 0),
        "config_only": by_status.get("config_only", 0),
        "degraded": by_status.get("degraded", 0),
        "rate_limited": by_status.get("rate_limited", 0),
        "auth_error": by_status.get("auth_error", 0),
        "missing_secret": by_status.get("missing_secret", 0),
        "skipped_daily_circuit": by_status.get("skipped_daily_circuit", 0),
        "critical_failures": len(critical_failures),
        "healthy_or_config_only": by_status.get("ok", 0) + by_status.get("config_only", 0) + by_status.get("skipped_daily_circuit", 0),
    })
    report["summary"] = summary
    report["by_status"] = by_status
    report["by_group"] = by_group
    report["critical_failures"] = critical_failures


def _rewrite_health_if_needed() -> None:
    if not _sportlogic_circuit_open() or not HEALTH_JSON.exists():
        return
    try:
        report = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return
    changed = False
    for row in report.get("results", []) if isinstance(report.get("results"), list) else []:
        if row.get("provider") != "sportlogic":
            continue
        row.update({
            "status": "skipped_daily_circuit",
            "critical": False,
            "configured": False,
            "requests": 0,
            "useful_rows": 0,
            "http_statuses": [],
            "latency_ms": None,
            "message": "SportLogic daily circuit is open; health check skipped before HTTP call.",
            "details": {**dict(row.get("details") or {}), "daily_circuit_open": True},
        })
        changed = True
    if not changed:
        return
    recs = [x for x in report.get("recommendations", []) if isinstance(x, str)]
    recs.append("SportLogic daily circuit is open; health probe was skipped to avoid spending exhausted quota.")
    report["recommendations"] = recs
    _recount(report)
    HEALTH_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # A compact markdown replacement is enough for the artifact; the full JSON carries details.
    lines = [
        "# API Health Run",
        "",
        f"- Created UTC: `{report.get('created_at_utc')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- Providers checked: **{report.get('summary', {}).get('providers_checked')}**",
        f"- OK: **{report.get('summary', {}).get('ok')}**",
        f"- Config-only/skipped: **{report.get('summary', {}).get('healthy_or_config_only')}**",
        f"- Critical failures: **{report.get('summary', {}).get('critical_failures')}**",
        "",
        "## Provider results",
        "",
        "| Provider | Group | Status | Requests | Useful rows | Message |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in report.get("results", []):
        lines.append(f"| `{row.get('provider')}` | `{row.get('group')}` | `{row.get('status')}` | {row.get('requests', 0)} | {row.get('useful_rows', 0)} | {str(row.get('message', '')).replace('|', '/')} |")
    HEALTH_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run() -> None:
    if not _truthy(os.getenv("API_HEALTH_DURING_RUN_ENABLED"), True):
        return
    script = Path("scripts/api_health_run.py")
    if not script.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--mode", os.getenv("API_HEALTH_MODE", "quick"), "--output-dir", ".data/exports"],
            check=False,
            timeout=int(float(os.getenv("API_HEALTH_GUARD_TIMEOUT_SECONDS", "55") or 55)),
            env=_subprocess_env(),
        )
        _rewrite_health_if_needed()
    except Exception as exc:
        print(f"[api-health-guard] skipped after error: {type(exc).__name__}: {exc}", flush=True)


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _is_provider_budget_process():
        return False
    setattr(sys, PATCH_MARKER, True)
    atexit.register(_run)
    print("[api-health-guard] enabled after provider budget policy", flush=True)
    return True
