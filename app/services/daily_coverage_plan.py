from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_assignments import build_assignments
from app.services.daily_coverage_common import (
    DAY_DIR,
    LEDGER_PATH,
    PHASE_TARGETS,
    PLAN_PATH,
    PROVIDER_TIMEOUTS,
    as_int,
    atomic_write,
    canonical_source,
    ledger_path,
    load,
    select_inventory,
    state_path,
    target_date,
)
from app.services.daily_coverage_ranking import coverage_summary, rank_inventory


def _next_run(date_key: str, now: datetime) -> tuple[int, str]:
    path = state_path(date_key)
    state = load(path, {})
    if not isinstance(state, dict) or state.get("date_local") != date_key:
        state = {"date_local": date_key, "run_ids": [], "run_index": 0}
    run_id = str(
        os.getenv("GITHUB_RUN_ID") or f"local-{now.strftime('%Y%m%dT%H%M%S')}"
    )
    run_ids = [str(value) for value in state.get("run_ids") or []]
    if run_id not in run_ids:
        run_ids.append(run_id)
        state["run_index"] = as_int(state.get("run_index")) + 1
    state.update(
        {
            "run_ids": run_ids[-32:],
            "last_run_id": run_id,
            "last_run_at_utc": now.isoformat(),
        }
    )
    atomic_write(path, state)
    return max(1, as_int(state.get("run_index"), 1)), run_id


def prepare_daily_coverage(now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    date_key = target_date(current)
    inventory_path, rows = select_inventory(date_key)
    ledger = load(ledger_path(date_key), {})
    if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
        ledger = {"date_local": date_key, "matches": {}, "runs": []}
    ranked = rank_inventory(rows, ledger, current, date_key)
    run_index, run_id = _next_run(date_key, current)
    target_count = min(len(ranked), PHASE_TARGETS[min(run_index, 3) - 1])
    targets = ranked[:target_count]
    assignments = build_assignments(targets, run_index)
    os.environ["SSTATS_PARI_DETAIL_MATCH_LIMIT"] = str(
        len(assignments["sstats_pari"]["offers"])
    )
    os.environ.setdefault("SSTATS_PARI_CONCURRENCY", "12")
    os.environ.setdefault("SSTATS_PARI_TIMEOUT_SECONDS", "7")
    os.environ.setdefault("CLUBELO_CONTEXT_MATCH_LIMIT", "300")
    plan = {
        "status": "ok" if ranked else "inventory_missing_or_empty",
        "created_at_utc": current.isoformat(),
        "date_local": date_key,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "inventory_rows_seen": len(rows),
        "top_inventory_matches": len(ranked),
        "run_id": run_id,
        "run_index": run_index,
        "phase": min(run_index, 3),
        "phase_targets": list(PHASE_TARGETS),
        "phase_cumulative_target": target_count,
        "min_odds_sources": 2,
        "min_context_sources": 2,
        "coverage_before": coverage_summary(ranked),
        "target_coverage_before": coverage_summary(targets),
        "target_match_keys": [row["match_key"] for row in targets],
        "assignments": assignments,
        "provider_timeouts_seconds": PROVIDER_TIMEOUTS,
        "matches": {
            row["match_key"]: {
                key: row.get(key)
                for key in (
                    "home_team",
                    "away_team",
                    "league_name",
                    "kickoff_utc",
                    "hours_to_kickoff",
                    "time_bucket",
                    "odds_sources",
                    "context_sources",
                    "line_deficit",
                    "context_deficit",
                )
            }
            for row in ranked
        },
        "independence_policy": {
            "odds_api_io_accounts_count_as_one_source": True,
            "bookmakers_do_not_count_as_provider_sources": True,
            "synthetic_inventory_context_does_not_count": True,
        },
        "publication_contract_relaxed": False,
    }
    atomic_write(PLAN_PATH, plan)
    atomic_write(DAY_DIR / f"daily-coverage-plan-{date_key}.json", plan)
    ledger.setdefault("runs", []).append(
        {
            "run_id": run_id,
            "run_index": run_index,
            "planned_at_utc": current.isoformat(),
            "cumulative_target": target_count,
        }
    )
    ledger["runs"] = ledger["runs"][-32:]
    ledger["updated_at_utc"] = current.isoformat()
    atomic_write(ledger_path(date_key), ledger)
    atomic_write(LEDGER_PATH, ledger)
    os.environ["HARIZON_DAILY_COVERAGE_PLAN_PATH"] = str(PLAN_PATH)
    return plan


def load_plan() -> dict[str, Any]:
    value = load(PLAN_PATH, {})
    return value if isinstance(value, dict) else {}


def provider_timeout(name: str) -> float | None:
    provider = canonical_source(name)
    try:
        return max(
            5.0,
            float(
                os.getenv(f"HARIZON_{provider.upper()}_WALL_SECONDS")
                or PROVIDER_TIMEOUTS.get(provider)
            ),
        )
    except (TypeError, ValueError):
        return None


def filter_matches(provider_name: str, method_name: str, matches: Any) -> Any:
    if not isinstance(matches, list) or not matches:
        return matches
    provider = canonical_source(provider_name)
    role = "offers" if "offer" in method_name.lower() else "context"
    assignments = load_plan().get("assignments") or {}
    if provider not in assignments or role not in (assignments.get(provider) or {}):
        return matches
    keys = set((assignments.get(provider) or {}).get(role) or [])
    return (
        [
            match
            for match in matches
            if str(getattr(match, "match_key", "")) in keys
        ]
        if keys
        else []
    )


__all__ = [
    "coverage_summary",
    "filter_matches",
    "load_plan",
    "prepare_daily_coverage",
    "provider_timeout",
    "rank_inventory",
]
