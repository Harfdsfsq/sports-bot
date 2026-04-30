from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
STATE_PATH = ROOT / ".data" / "autorun-state.json"
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-autorun-state.json"
UTC = timezone.utc
SUCCESS_STATUSES = {"success", "recovered"}


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def utc_now() -> datetime:
    return datetime.now(UTC)


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_state() -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("timezone", str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"))
    state.setdefault("slots", {})
    if not isinstance(state.get("slots"), dict):
        state["slots"] = {}
    return state


def save_state(state: dict[str, Any]) -> None:
    state["updated_at_utc"] = utc_now().isoformat()
    write_json(STATE_PATH, state)
    write_json(EXPORT_PATH, state)


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def normalize_slot_key(value: Any, tz: ZoneInfo | None = None) -> str:
    tz = tz or app_tz()
    parsed = parse_dt(value)
    if parsed is None:
        return latest_slot_key(tz=tz)
    return parsed.astimezone(tz).replace(second=0, microsecond=0).isoformat()


def latest_slot_dt(now: datetime | None = None, tz: ZoneInfo | None = None) -> datetime:
    tz = tz or app_tz()
    local_now = (now or utc_now()).astimezone(tz)
    slot_hour = local_now.hour if local_now.hour % 2 == 0 else local_now.hour - 1
    return local_now.replace(hour=slot_hour, minute=0, second=0, microsecond=0)


def latest_slot_key(now: datetime | None = None, tz: ZoneInfo | None = None) -> str:
    return latest_slot_dt(now=now, tz=tz).isoformat()


def previous_slot_dt(slot: datetime, steps: int = 1) -> datetime:
    return slot - timedelta(hours=2 * max(1, steps))


def next_slot_dt(slot: datetime, steps: int = 1) -> datetime:
    return slot + timedelta(hours=2 * max(1, steps))


def iter_slot_keys(start: datetime, end: datetime, tz: ZoneInfo | None = None) -> list[str]:
    tz = tz or app_tz()
    cur = latest_slot_dt(start.astimezone(tz), tz)
    if cur > start.astimezone(tz):
        cur -= timedelta(hours=2)
    out: list[str] = []
    end_local = end.astimezone(tz)
    while cur <= end_local:
        out.append(cur.isoformat())
        cur += timedelta(hours=2)
    return out


def slot_record(state: dict[str, Any], slot_key: str) -> dict[str, Any]:
    slots = state.setdefault("slots", {})
    if not isinstance(slots, dict):
        slots = {}
        state["slots"] = slots
    row = slots.get(slot_key)
    if not isinstance(row, dict):
        row = {}
        slots[slot_key] = row
    return row


def slot_completed(state: dict[str, Any], slot_key: str) -> bool:
    row = (state.get("slots") or {}).get(slot_key)
    return isinstance(row, dict) and str(row.get("status") or "").lower() in SUCCESS_STATUSES


def slot_pending(state: dict[str, Any], slot_key: str, now: datetime | None = None, ttl_minutes: int = 90) -> bool:
    row = (state.get("slots") or {}).get(slot_key)
    if not isinstance(row, dict):
        return False
    status = str(row.get("status") or "").lower()
    if status not in {"running", "dispatched"}:
        return False
    stamp = parse_dt(row.get("started_at_utc") or row.get("dispatched_at_utc"))
    if stamp is None:
        return False
    age = ((now or utc_now()).astimezone(UTC) - stamp.astimezone(UTC)).total_seconds() / 60.0
    return age < ttl_minutes


def prune_old_slots(state: dict[str, Any], keep_days: int = 14) -> None:
    slots = state.get("slots")
    if not isinstance(slots, dict):
        return
    cutoff = utc_now() - timedelta(days=max(1, keep_days))
    for key in list(slots):
        dt = parse_dt(key)
        if dt is not None and dt.astimezone(UTC) < cutoff:
            slots.pop(key, None)
