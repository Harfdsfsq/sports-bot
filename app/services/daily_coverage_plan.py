from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_assignments import build_assignments
from app.services.daily_coverage_common import (
    DAY_DIR,
    LEDGER_PATH,
    PHASE_TARGETS,
    PLAN_PATH,
    PROVIDER_TIMEOUTS,
    app_timezone,
    as_float,
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
from app.services.focused_alpha import (
    enabled as focused_alpha_enabled,
)
from app.services.focused_alpha import (
    phase_targets as focused_alpha_phase_targets,
)
from app.services.focused_alpha import (
    select_focus_cohort,
)
from app.utils import canonicalize_team_name, parse_datetime

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _focus_selection(
    ranked: list[dict[str, Any]],
    *,
    current: datetime,
    run_index: int,
) -> tuple[list[dict[str, Any]], list[int], dict[str, Any]]:
    """Return this run's adaptive provider target cohort.

    The fixed daily inventory remains a discovery/identity ledger.  Expensive API
    calls are assigned only to the best information-value matches.  There is no
    minimum cohort size and no publication quota.
    """

    if not focused_alpha_enabled():
        phase_values = list(PHASE_TARGETS)
        target_count = min(len(ranked), phase_values[min(run_index, 3) - 1])
        return ranked[:target_count], phase_values, {
            "mode": "legacy_fixed_coverage",
            "selected_rows": target_count,
            "fixed_coverage_quota": True,
            "publication_contract_relaxed": False,
        }
    try:
        focus = select_focus_cohort(ranked, now=current)
        selected = list(focus.get("rows") or [])
        phase_values = list(focused_alpha_phase_targets())
        target_count = min(len(selected), phase_values[min(run_index, 3) - 1])
        report = dict(focus.get("report") or {})
        report["phase_selected_rows"] = target_count
        report["run_index"] = run_index
        return selected[:target_count], phase_values, report
    except Exception as exc:
        # A scoring/report failure must not make the production runner blind.  The
        # fallback is deliberately smaller than the old 300-match assignment.
        phase_values = [40, 70, 100]
        target_count = min(len(ranked), phase_values[min(run_index, 3) - 1])
        return ranked[:target_count], phase_values, {
            "mode": "focused_alpha_safe_fallback",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "selected_rows": target_count,
            "fixed_coverage_quota": False,
            "publication_contract_relaxed": False,
        }


def _assignment_total(assignments: dict[str, Any]) -> int:
    return sum(
        len(keys)
        for roles in assignments.values()
        if isinstance(roles, dict)
        for keys in roles.values()
        if isinstance(keys, list)
    )


def _active_provider_rows(rows: list[dict[str, Any]]) -> int:
    collection_hours = max(
        4.0,
        as_float(
            os.getenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS")
            or os.getenv("DATA_COLLECTION_WINDOW_HOURS"),
            36.0,
        ),
    )
    return sum(
        1
        for row in rows
        if row.get("provider_assignment_eligible") is not False
        and 0.33
        <= as_float(row.get("hours_to_kickoff"), 999.0)
        <= collection_hours
    )


def prepare_daily_coverage(now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    date_key = target_date(current)
    inventory_path, rows = select_inventory(date_key)
    ledger = load(ledger_path(date_key), {})
    if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
        ledger = {"date_local": date_key, "matches": {}, "runs": []}
    ranked = rank_inventory(rows, ledger, current, date_key)
    run_index, run_id = _next_run(date_key, current)
    targets, active_phase_targets, focus_report = _focus_selection(
        ranked,
        current=current,
        run_index=run_index,
    )
    target_count = len(targets)
    assignments = build_assignments(targets, run_index)
    assignment_total = _assignment_total(assignments)
    active_provider_rows = _active_provider_rows(ranked)
    assignment_health = "ok"
    if active_provider_rows > 0 and target_count == 0:
        assignment_health = "blocked_active_inventory_without_focus_targets"
    elif target_count > 0 and assignment_total == 0:
        assignment_health = "blocked_focus_targets_without_provider_assignments"
    os.environ["SSTATS_PARI_DETAIL_MATCH_LIMIT"] = str(
        len(assignments["sstats_pari"]["offers"])
    )
    os.environ.setdefault("SSTATS_PARI_CONCURRENCY", "12")
    os.environ.setdefault("SSTATS_PARI_TIMEOUT_SECONDS", "7")
    os.environ["CLUBELO_CONTEXT_MATCH_LIMIT"] = str(
        len(assignments.get("clubelo", {}).get("context", []))
    )
    plan = {
        "status": (
            "inventory_missing_or_empty"
            if not ranked
            else assignment_health
        ),
        "created_at_utc": current.isoformat(),
        "date_local": date_key,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "inventory_rows_seen": len(rows),
        "top_inventory_matches": len(ranked),
        "discovery_universe_matches": len(ranked),
        "run_id": run_id,
        "run_index": run_index,
        "phase": min(run_index, 3),
        "phase_targets": active_phase_targets,
        "phase_cumulative_target": target_count,
        "min_odds_sources": 2,
        "min_context_sources": 2,
        "coverage_before": coverage_summary(ranked),
        "target_coverage_before": coverage_summary(targets),
        "target_match_keys": [row["match_key"] for row in targets],
        "assignments": assignments,
        "provider_assignment_health": {
            "status": assignment_health,
            "active_provider_eligible_rows": active_provider_rows,
            "focused_targets": target_count,
            "provider_role_assignments": assignment_total,
            "silent_empty_assignments_allowed": False,
        },
        "focused_alpha": focus_report,
        "coverage_objective": (
            "maximize_expected_information_and_risk_adjusted_decision_quality"
            if focused_alpha_enabled()
            else "fixed_2plus_coverage"
        ),
        "fixed_300_provider_target": not focused_alpha_enabled(),
        "publication_minimum_count": 0,
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
                    "focused_alpha_score",
                    "focused_alpha_exploration",
                    "focused_alpha_bootstrap",
                    "focused_alpha_selection_lane",
                )
            }
            for row in ranked
        },
        "independence_policy": {
            "odds_api_io_accounts_count_as_one_source": True,
            "bookmakers_do_not_count_as_provider_sources": True,
            "synthetic_inventory_context_does_not_count": True,
        },
        "match_identity_policy": {
            "inventory_key": "date|home|away",
            "runtime_key": "sport|sorted-team-pair|utc-date",
            "utc_and_local_dates_accepted": True,
            "team_pair_orientation_ignored": True,
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
            "selection_mode": focus_report.get("mode") or "unknown",
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


def _team_pair(home: Any, away: Any) -> tuple[str, str] | None:
    first = canonicalize_team_name(str(home or ""))
    second = canonicalize_team_name(str(away or ""))
    if not first or not second:
        return None
    return tuple(sorted((first, second)))


def _identity_from_key(value: Any) -> tuple[str, tuple[str, str]] | None:
    parts = [part.strip() for part in str(value or "").split("|")]
    if len(parts) >= 3 and _DATE_RE.fullmatch(parts[0]):
        pair = _team_pair(parts[1], parts[2])
        return (parts[0], pair) if pair else None
    if len(parts) >= 4 and _DATE_RE.fullmatch(parts[-1]):
        pair = _team_pair(parts[1], parts[2])
        return (parts[-1], pair) if pair else None
    return None


def _match_identities(match: Any) -> set[tuple[str, tuple[str, str]]]:
    identities: set[tuple[str, tuple[str, str]]] = set()
    direct = _identity_from_key(getattr(match, "match_key", ""))
    if direct:
        identities.add(direct)
    metadata = getattr(match, "metadata", {})
    if isinstance(metadata, dict):
        for key in (
            "match_key",
            "canonical_match_id",
            "semantic_match_key",
            "day_inventory_match_key",
        ):
            identity = _identity_from_key(metadata.get(key))
            if identity:
                identities.add(identity)
    pair = _team_pair(getattr(match, "home_team", ""), getattr(match, "away_team", ""))
    kickoff = getattr(match, "commence_time", None)
    if pair and kickoff is not None:
        try:
            parsed = parse_datetime(kickoff)
        except Exception:
            parsed = None
        if parsed is not None:
            identities.add((parsed.astimezone(UTC).date().isoformat(), pair))
            identities.add((parsed.astimezone(app_timezone()).date().isoformat(), pair))
    return identities


def filter_matches(provider_name: str, method_name: str, matches: Any) -> Any:
    if not isinstance(matches, list) or not matches:
        return matches
    provider = canonical_source(provider_name)
    role = "offers" if "offer" in method_name.lower() else "context"
    assignments = load_plan().get("assignments") or {}
    if provider not in assignments or role not in (assignments.get(provider) or {}):
        return matches
    keys = {str(value) for value in (assignments.get(provider) or {}).get(role) or [] if value}
    if not keys:
        return []
    assignment_identities = {
        identity for value in keys if (identity := _identity_from_key(value)) is not None
    }
    selected = []
    for match in matches:
        runtime_key = str(getattr(match, "match_key", ""))
        if runtime_key in keys:
            selected.append(match)
            continue
        if assignment_identities.intersection(_match_identities(match)):
            selected.append(match)
    return selected


__all__ = [
    "coverage_summary",
    "filter_matches",
    "load_plan",
    "prepare_daily_coverage",
    "provider_timeout",
    "rank_inventory",
]
