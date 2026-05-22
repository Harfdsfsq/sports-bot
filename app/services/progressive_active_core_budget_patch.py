from __future__ import annotations

"""Patch progressive coverage plan to show the *active* core provider contract.

SportLogic is an optional line provider. If the quota contract gives it 0
requests, the report must not display it as an active core line source. It may be
listed as excluded instead. This is reporting/diagnostics only; it does not make
any non-core source count as a strict line source.
"""

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
CONTRACT_PATH = EXPORT_DIR / "latest-per-run-api-quota-contract.json"
REPORT_PATH = EXPORT_DIR / "latest-progressive-active-core-budget-patch.json"

BASE_CORE_ODDS = {"odds_api_io", "bzzoiro", "sportlogic"}
BASE_CORE_CONTEXT = {"bzzoiro", "sstats"}
_INSTALLED = False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    return {}


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _env_disabled(provider: str) -> bool:
    p = provider.upper()
    for key in (f"ENABLE_{p}", f"{p}_ENABLED"):
        raw = os.getenv(key)
        if raw is not None and str(raw).strip().lower() in {"0", "false", "no", "off"}:
            return True
    return False


def _grant(provider: str) -> int | None:
    contract = _read_json(CONTRACT_PATH)
    grants = contract.get("per_run_grants") if isinstance(contract.get("per_run_grants"), dict) else {}
    if provider in grants:
        return _to_int(grants.get(provider), None)
    p = provider.upper()
    for key in (
        f"{p}_REQUEST_BUDGET_GRANTED",
        f"{p}_PER_RUN_MAX",
        f"{p}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{p}_REQUESTS_MAX_PER_RUN",
        f"{p}_MAX_REQUESTS_PER_RUN",
    ):
        if os.getenv(key) not in (None, ""):
            return _to_int(os.getenv(key), None)
    return None


def _active_provider_set(providers: set[str]) -> tuple[set[str], list[dict[str, Any]]]:
    active: set[str] = set()
    excluded: list[dict[str, Any]] = []
    for provider in sorted(providers):
        grant = _grant(provider)
        disabled = _env_disabled(provider)
        if disabled or grant == 0:
            excluded.append({
                "provider": provider,
                "reason": "disabled" if disabled else "zero_budget",
                "grant": grant,
            })
        else:
            active.add(provider)
    return active, excluded


def patch_plan_file() -> dict[str, Any]:
    plan = _read_json(PLAN_PATH)
    active_odds, excluded_odds = _active_provider_set(BASE_CORE_ODDS)
    active_context, excluded_context = _active_provider_set(BASE_CORE_CONTEXT)
    if not plan:
        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "plan_missing",
            "active_core_odds_providers": sorted(active_odds),
            "active_core_context_providers": sorted(active_context),
            "excluded_core_providers": excluded_odds + excluded_context,
        }
        _write_json(REPORT_PATH, payload)
        return payload

    contract = plan.get("contract") if isinstance(plan.get("contract"), dict) else {}
    contract["core_odds_providers"] = sorted(active_odds)
    contract["core_context_providers"] = sorted(active_context)
    contract["core_providers"] = sorted(active_odds | active_context)
    contract["excluded_core_providers"] = excluded_odds + excluded_context
    contract["reason"] = "active core excludes disabled/zero-budget providers from the current run"
    plan["contract"] = contract
    plan.setdefault("diagnostics", {})
    if isinstance(plan["diagnostics"], dict):
        plan["diagnostics"]["active_core_budget_patch"] = "applied"
        plan["diagnostics"]["active_core_odds_providers"] = sorted(active_odds)
        plan["diagnostics"]["excluded_core_providers"] = excluded_odds + excluded_context
    _write_json(PLAN_PATH, plan)

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "applied",
        "active_core_odds_providers": sorted(active_odds),
        "active_core_context_providers": sorted(active_context),
        "excluded_core_providers": excluded_odds + excluded_context,
    }
    _write_json(REPORT_PATH, payload)
    return payload


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True

    report: dict[str, Any] = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        # If the core finalizer is imported later, the atexit plan patch still
        # runs after it writes latest-progressive-coverage-plan.json.
        atexit.register(patch_plan_file)
        report.update(patch_plan_file())
        report["status"] = "installed"
    except Exception as exc:
        report.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, report)
    return report
