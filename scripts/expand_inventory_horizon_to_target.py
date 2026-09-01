from __future__ import annotations

"""Expand production inventory over the configured run horizon.

The normal day expander only accepts rows whose local date is exactly
DAY_INVENTORY_TARGET_DATE.  In production the bot publishes over the next run
horizon, not just midnight-to-midnight; when the current day has fewer than 300
known fixtures, this script fills the 300-target pool with already-known fixtures
from the next RUN_DAYS_AHEAD days.  It does not invent odds/context and does not
publish anything.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts import expand_day_inventory_to_target as day_expand

UTC = timezone.utc
ROOT = Path(".").resolve()
DAY_DIR = ROOT / ".data" / "day_inventory"
CACHE_DAY_DIR = ROOT / ".data" / "cache" / "day_inventory"
REPORT_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-target-expand.json"


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return max(minimum, int(default))
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, int(default))


def parse_day(text: str) -> datetime:
    return datetime.fromisoformat(text[:10]).replace(tzinfo=UTC)


def horizon_days() -> int:
    raw = os.getenv("DAY_INVENTORY_HORIZON_DAYS") or os.getenv("DAY_INVENTORY_TARGET_HORIZON_DAYS") or os.getenv("RUN_DAYS_AHEAD") or "2"
    return max(1, min(env_int("_UNUSED", int(float(raw)), 1), 4))


def in_horizon(row: dict[str, Any], start_day: str, days: int) -> bool:
    d = day_expand.row_date(row)
    if not d:
        return True
    try:
        current = parse_day(d)
        start = parse_day(start_day)
    except Exception:
        return d == start_day
    return start <= current < start + timedelta(days=days)


def collect_rows(start_day: str, days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    parse_errors: list[str] = []
    for path in day_expand.candidate_paths(start_day):
        payload = day_expand.load_json(path, None)
        if payload is None:
            if path.exists():
                parse_errors.append(str(path))
            continue
        accepted = 0
        for row in day_expand.rows_from_payload(payload):
            if not isinstance(row, dict) or not in_horizon(row, start_day, days):
                continue
            key = day_expand.row_key(row)
            if not key:
                continue
            by_key[key] = day_expand.merge_row(by_key[key], row) if key in by_key else dict(row)
            accepted += 1
        if accepted:
            source_counts[str(path)] = accepted
    return sorted(by_key.values(), key=day_expand.kickoff_sort_key), {"source_counts": source_counts, "parse_errors": parse_errors[:30]}


def best_existing(start_day: str, days: int) -> dict[str, Any]:
    best: dict[str, Any] = {"date_local": start_day, "matches": [], "counts": {}}
    best_count = -1
    for path in day_expand.candidate_paths(start_day):
        payload = day_expand.load_json(path, {})
        if not isinstance(payload, dict):
            continue
        raw_rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        rows = [r for r in raw_rows if isinstance(r, dict) and in_horizon(r, start_day, days)]
        if len(rows) > best_count:
            best_count = len(rows)
            best = dict(payload)
            best["matches"] = rows
    if not isinstance(best.get("counts"), dict):
        best["counts"] = {}
    return best


def write_aliases(payload: dict[str, Any], day: str) -> list[str]:
    changed: list[str] = []
    for path in (
        DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json",
        CACHE_DAY_DIR / f"{day}.json", CACHE_DAY_DIR / "today.json", CACHE_DAY_DIR / "current.json", CACHE_DAY_DIR / "latest.json",
    ):
        day_expand.write_json(path, payload)
        changed.append(str(path))
    return changed


def main() -> int:
    start_day = day_expand.target_date()
    days = horizon_days()
    target = env_int("DAY_INVENTORY_TARGET_SIZE", env_int("DAY_INVENTORY_MAX_MATCHES", 300, 1), 1)
    rows, diagnostics = collect_rows(start_day, days)
    existing = best_existing(start_day, days)
    existing_rows = existing.get("matches") if isinstance(existing.get("matches"), list) else []
    selected_from_collected = rows[:target] if target > 0 else rows
    if len(selected_from_collected) >= len(existing_rows):
        selected = selected_from_collected
        payload = dict(existing)
        payload["matches"] = selected
    else:
        selected = existing_rows
        payload = dict(existing)
        payload["matches"] = selected
    counts = payload.setdefault("counts", {})
    counts["matches_total"] = len(selected)
    counts["matches_after_target_expand"] = len(selected)
    counts["target_matches"] = target
    counts["target_shortfall"] = max(0, target - len(selected))
    counts["target_expand_rows_collected"] = len(rows)
    counts["target_expand_existing_before"] = len(existing_rows)
    counts["target_expand_horizon_days"] = days
    payload["date_local"] = start_day
    payload["inventory_horizon_days"] = days
    payload["target_matches"] = target
    payload["target_expand_updated_at_utc"] = datetime.now(UTC).isoformat()
    payload["target_expand_status"] = "ok_target_met" if len(selected) >= target else "partial_known_rows_only"
    changed = write_aliases(payload, start_day)
    highwater = day_expand.write_highwater(payload, start_day)
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "horizon_inventory_expand_v1",
        "target_date": start_day,
        "horizon_days": days,
        "target": target,
        "existing_before": len(existing_rows),
        "rows_collected": len(rows),
        "selected_from_collected": len(selected_from_collected),
        "matches_after": len(selected),
        "target_shortfall": max(0, target - len(selected)),
        "target_timezone": str(day_expand.app_time_zone()),
        "status": payload["target_expand_status"],
        "changed_paths": changed,
        "highwater_paths": highwater,
        **diagnostics,
    }
    day_expand.write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
