from __future__ import annotations

"""Repair refresh-plan counters after inventory priority calculation.

`update_day_inventory_priority_and_line_state.py` correctly sets per-row
`refresh_plan.needs_odds_refresh`, but its top-level counter historically meant
"rows without odds coverage". In Telegram this looked like the bot wanted to
refresh almost every active match even when many rows already had fresh lines.

This script makes the exported counters strict and auditable:

* matches_needing_odds_refresh = rows whose row.refresh_plan.needs_odds_refresh is true;
* matches_missing_odds_coverage = active rows without any odds coverage;
* matches_stale_odds_refresh = active rows that have odds but are stale by policy;
* final_pre_kickoff_checks / no_more_regular_run_before_kickoff are recomputed
  from row-level refresh plans.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import write_current_aliases

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
REFRESH_PLAN_PATH = EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
PRIORITY_STATE_PATH = EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json"
OUT_PATH = EXPORT_DIR / "latest-inventory-refresh-plan-count-repair.json"


def app_tz() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def now_utc_from_debug() -> datetime:
    """Return a fresh run timestamp, not a stale cached debug timestamp."""
    for env_name in ("HARIZON_RUN_NOW_UTC", "RUN_NOW_UTC", "CURRENT_TIME_UTC"):
        dt = parse_dt(os.getenv(env_name))
        if dt is not None:
            return dt

    wall = datetime.now(UTC)
    debug = load_json(ROOT / ".logs" / "debug-last-run.json", {})
    summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else {}
    candidates = [summary.get("current_time_utc"), debug.get("current_time_utc") if isinstance(debug, dict) else None]
    max_age_min = max(1, env_int("MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES", 360))
    allow_stale = env_bool("ALLOW_STALE_DEBUG_TIME_FOR_INVENTORY", False)
    for value in candidates:
        dt = parse_dt(value)
        if dt is None:
            continue
        age_min = abs((wall - dt).total_seconds()) / 60.0
        if age_min <= max_age_min or allow_stale:
            return dt
    return wall


def target_date(now: datetime) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return now.astimezone(app_tz()).date().isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def refresh_bool(row: dict[str, Any], key: str) -> bool:
    plan = row.get("refresh_plan") if isinstance(row.get("refresh_plan"), dict) else {}
    return bool(plan.get(key))


def active(row: dict[str, Any]) -> bool:
    minutes = row.get("minutes_to_kickoff")
    if minutes in (None, ""):
        kickoff = parse_dt(row.get("kickoff_utc") or row.get("commence_time"))
        if kickoff is None:
            return False
        minutes = (kickoff - now_utc_from_debug()).total_seconds() / 60.0
    return safe_float(minutes, -1.0) >= 0.0


def main() -> int:
    now = now_utc_from_debug()
    local_date = target_date(now)
    inventory_path = DAY_INV_DIR / f"{local_date}.json"
    inventory = load_json(inventory_path, {})
    rows = [row for row in inventory.get("matches", []) if isinstance(row, dict)] if isinstance(inventory, dict) else []

    active_rows = [row for row in rows if active(row)]
    needs_odds_refresh = [row for row in active_rows if refresh_bool(row, "needs_odds_refresh")]
    final_checks = [row for row in active_rows if refresh_bool(row, "final_pre_kickoff_check_required")]
    no_more = [row for row in active_rows if refresh_bool(row, "no_more_regular_run_before_kickoff")]
    missing_odds = [row for row in active_rows if not bool((row.get("coverage") or {}).get("odds"))]
    stale_odds = [row for row in needs_odds_refresh if bool((row.get("coverage") or {}).get("odds"))]

    counters = {
        "active_matches": len(active_rows),
        "matches_needing_odds_refresh": len(needs_odds_refresh),
        "matches_missing_odds_coverage": len(missing_odds),
        "matches_stale_odds_refresh": len(stale_odds),
        "final_pre_kickoff_checks": len(final_checks),
        "no_more_regular_run_before_kickoff": len(no_more),
    }

    plan = load_json(REFRESH_PLAN_PATH, {})
    if isinstance(plan, dict):
        plan.update(counters)
        plan["count_semantics"] = {
            "matches_needing_odds_refresh": "row.refresh_plan.needs_odds_refresh == true",
            "matches_missing_odds_coverage": "active row without coverage.odds",
            "matches_stale_odds_refresh": "active row with odds but row.refresh_plan.needs_odds_refresh == true",
        }
        plan["count_repaired_at_utc"] = now.isoformat()
        write_json(REFRESH_PLAN_PATH, plan)

    state = load_json(PRIORITY_STATE_PATH, {})
    if isinstance(state, dict):
        refresh_plan = state.get("refresh_plan") if isinstance(state.get("refresh_plan"), dict) else {}
        refresh_plan.update(counters)
        refresh_plan["count_semantics"] = plan.get("count_semantics") if isinstance(plan, dict) else {}
        refresh_plan["count_repaired_at_utc"] = now.isoformat()
        state["refresh_plan"] = refresh_plan
        write_json(PRIORITY_STATE_PATH, state)

    if isinstance(inventory, dict):
        sources = inventory.setdefault("sources", {})
        if isinstance(sources, dict):
            sources["refresh_plan_count_repair"] = {"updated_at_utc": now.isoformat(), **counters}
        write_json(inventory_path, inventory)
        alias_update = write_current_aliases(ROOT, local_date, inventory, write_json)
    else:
        alias_update = {"status": "skipped", "reason": "inventory_missing"}

    report = {
        "status": "ok",
        "date_local": local_date,
        "updated_at_utc": now.isoformat(),
        "alias_update": alias_update,
        **counters,
        "notes": [
            "Top-level refresh counters now match row-level refresh_plan flags.",
            "Missing coverage and stale/final-refresh are separated so Telegram no longer exaggerates odds refresh load.",
        ],
    }
    write_json(OUT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
