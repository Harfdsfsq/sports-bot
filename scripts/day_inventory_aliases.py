from __future__ import annotations

"""Helpers for current day-inventory aliases.

Several maintenance scripts can build or repair inventories for arbitrary dates.
The bot-facing aliases (`latest.json`, `current.json`, `today.json`) must stay
pointed at the current local run date unless a workflow explicitly opts into
rewriting them for a historical or future target.
"""

import json
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


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _load_json(path: Path) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return None


def _can_write_alias(path: Path, payload: Any) -> tuple[bool, str, int, int]:
    if _truthy(os.getenv("DAY_INVENTORY_FORCE_ALIAS_SHRINK")):
        return True, "forced", 0, 0
    incoming_rows = len(_rows(payload))
    existing_payload = _load_json(path)
    existing_rows = len(_rows(existing_payload))
    if existing_rows > 0 and incoming_rows < existing_rows:
        return False, "prevented_alias_shrink", existing_rows, incoming_rows
    return True, "ok", existing_rows, incoming_rows


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
    written: list[str] = []
    skipped: list[dict[str, Any]] = []
    for path in paths:
        allowed, reason, existing_rows, incoming_rows = _can_write_alias(path, payload)
        if not allowed:
            skipped.append({
                "path": str(path),
                "reason": reason,
                "existing_rows": existing_rows,
                "incoming_rows": incoming_rows,
            })
            continue
        write_json(path, payload)
        written.append(str(path))
    status = "ok" if not skipped else ("partial_no_shrink" if written else "skipped_no_shrink")
    return {
        "status": status,
        "target_date": target_date,
        "paths": written,
        "skipped": skipped,
    }
