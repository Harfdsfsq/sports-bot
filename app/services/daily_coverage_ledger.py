from __future__ import annotations

import os
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import (
    LEDGER_PATH, REPORT_PATH, atomic_write, canonical_source, independent_sources,
    ledger_path, load, select_inventory, target_date,
)
from app.services.daily_coverage_plan import coverage_summary, load_plan, rank_inventory

_LOCK = threading.RLock()


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def record_provider_result(provider_name: str, method_name: str, data: Any, stats: Any = None) -> None:
    provider = canonical_source(provider_name)
    role = "odds" if "offer" in method_name.lower() else "context"
    if role == "context" and provider in {"", "unknown", "day_inventory", "merged", "self_history"}:
        return
    if not isinstance(data, dict):
        return
    keys = [str(key) for key, value in data.items() if value]
    if not keys:
        return
    now, date_key = datetime.now(UTC), target_date()
    path = ledger_path(date_key)
    with _LOCK:
        ledger = load(path, {})
        if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
            ledger = {"date_local": date_key, "matches": {}, "runs": []}
        match_rows = ledger.setdefault("matches", {})
        field = "odds_sources" if role == "odds" else "context_sources"
        for key in keys:
            row = match_rows.setdefault(key, {})
            row[field] = independent_sources(list(row.get(field) or []) + [provider], role=role)
            row[f"last_{provider}_{role}_observed_at_utc"] = now.isoformat()
        runs = ledger.setdefault("provider_runs", [])
        runs.append({
            "run_id": str(os.getenv("GITHUB_RUN_ID") or ""), "provider": provider,
            "method": method_name, "role": role, "matched": len(keys),
            "observed_at_utc": now.isoformat(), "stats": _serialize(stats or {}),
        })
        ledger["provider_runs"] = runs[-128:]
        ledger["updated_at_utc"] = now.isoformat()
        atomic_write(path, ledger)
        atomic_write(LEDGER_PATH, ledger)


def finalize_daily_coverage(summary: dict[str, Any] | None = None) -> dict[str, Any]:
    now, date_key = datetime.now(UTC), target_date()
    plan, ledger = load_plan(), load(ledger_path(date_key), {})
    inventory_path, rows = select_inventory(date_key)
    ranked = rank_inventory(rows, ledger if isinstance(ledger, dict) else {}, now, date_key)
    report = {
        "status": "ok", "created_at_utc": now.isoformat(), "date_local": date_key,
        "run_id": str(os.getenv("GITHUB_RUN_ID") or ""), "run_index": plan.get("run_index"),
        "phase_cumulative_target": plan.get("phase_cumulative_target"),
        "inventory_path": str(inventory_path) if inventory_path else None,
        "coverage_after": coverage_summary(ranked),
        "goal": {"matches": 300, "min_independent_odds_sources": 2, "min_independent_context_sources": 2},
        "provider_assignments": {provider: {role: len(keys or []) for role, keys in roles.items()} for provider, roles in (plan.get("assignments") or {}).items() if isinstance(roles, dict)},
        "runner_summary_available": isinstance(summary, dict),
        "publication_contract_relaxed": False,
    }
    atomic_write(REPORT_PATH, report)
    return report
