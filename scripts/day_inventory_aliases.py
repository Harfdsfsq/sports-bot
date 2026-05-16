from __future__ import annotations

"""Helpers for current day-inventory aliases.

Several maintenance scripts can build or repair inventories for arbitrary dates.
The bot-facing aliases (`latest.json`, `current.json`, `today.json`) must stay
pointed at the current local run date unless a workflow explicitly opts into
rewriting them for a historical or future target.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

UTC = timezone.utc


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def current_local_date() -> str:
    override = str(os.getenv("DAY_INVENTORY_CURRENT_DATE") or "").strip()
    if override:
        return override
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def should_update_current_aliases(target_date: str) -> bool:
    if str(os.getenv("DAY_INVENTORY_SKIP_ALIAS_UPDATE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if str(os.getenv("DAY_INVENTORY_ALLOW_NON_CURRENT_ALIASES") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return str(target_date or "").strip() == current_local_date()


def current_alias_paths(root: Path) -> list[Path]:
    inv_dir = root / ".data" / "day_inventory"
    return [inv_dir / "latest.json", inv_dir / "current.json", inv_dir / "today.json"]


def write_current_aliases(
    root: Path,
    target_date: str,
    payload: Any,
    write_json: Callable[[Path, Any], None],
) -> dict[str, Any]:
    if not should_update_current_aliases(target_date):
        return {
            "status": "skipped",
            "reason": "target_date_is_not_current",
            "target_date": target_date,
            "current_date": current_local_date(),
        }
    paths = current_alias_paths(root)
    for path in paths:
        write_json(path, payload)
    return {
        "status": "ok",
        "target_date": target_date,
        "paths": [str(path) for path in paths],
    }
