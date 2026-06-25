from __future__ import annotations

"""Normalize day-inventory matches into HARIZON time windows.

Required processing windows:
0-4h, 4-8h, 8-12h, 12-16h, 16-20h, 20-24h, >24h.
The fields are added to inventory rows so later scripts can prioritize nearest
matches without depending on older coarse 0-6/6-12/12+ grouping.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
UTC = timezone.utc
OUT = ROOT / ".data" / "exports" / "latest-day-inventory-time-window-normalization.json"
WINDOWS = [
    (0, 4, "0_4h", 0),
    (4, 8, "4_8h", 1),
    (8, 12, "8_12h", 2),
    (12, 16, "12_16h", 3),
    (16, 20, "16_20h", 4),
    (20, 24, "20_24h", 5),
]
ORDER = ["0_4h", "4_8h", "8_12h", "12_16h", "16_20h", "20_24h", "gt_24h", "started", "unknown"]


def _tz() -> ZoneInfo:
    for name in (os.getenv("APP_TIMEZONE"), os.getenv("TZ"), "Europe/Moscow"):
        try:
            return ZoneInfo(str(name))
        except Exception:
            continue
    return ZoneInfo("Europe/Moscow")


def _target_date(now: datetime) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    return explicit or now.astimezone(_tz()).date().isoformat()


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_dt(value: Any) -> datetime | None:
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


def classify_time_window(kickoff: datetime | None, now: datetime) -> tuple[str, int, float | None]:
    if kickoff is None:
        return "unknown", len(ORDER) - 1, None
    hours = (kickoff - now).total_seconds() / 3600.0
    if hours < 0:
        return "started", ORDER.index("started"), hours
    for start, end, name, rank in WINDOWS:
        if start <= hours < end:
            return name, rank, hours
    return "gt_24h", ORDER.index("gt_24h"), hours


def _candidate_paths(local_date: str) -> list[Path]:
    base = ROOT / ".data" / "day_inventory"
    out = [base / f"{local_date}.json", base / "today.json", base / "latest.json", base / "current.json"]
    return [path for idx, path in enumerate(out) if path not in out[:idx]]


def _normalize_file(path: Path, now: datetime) -> dict[str, Any]:
    payload = _load_json(path, None)
    report: dict[str, Any] = {"path": str(path), "status": "skipped", "reason": "missing_or_invalid"}
    if not isinstance(payload, dict):
        return report
    rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    counts = {name: 0 for name in ORDER}
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
        bucket, rank, hours = classify_time_window(kickoff, now)
        before = (row.get("time_window_bucket"), row.get("processing_group"), row.get("time_window_rank"))
        row["time_window_bucket"] = bucket
        row["processing_group"] = bucket
        row["time_window_rank"] = rank
        row["hours_to_kickoff"] = round(hours, 4) if hours is not None else None
        row["time_window_sort_key"] = [rank, abs(hours) if hours is not None else 999999]
        after = (row.get("time_window_bucket"), row.get("processing_group"), row.get("time_window_rank"))
        changed += int(before != after)
        counts[bucket] = counts.get(bucket, 0) + 1
    payload.setdefault("sources", {})
    if isinstance(payload["sources"], dict):
        payload["sources"]["time_window_normalizer"] = {
            "updated_at_utc": now.isoformat(),
            "windows": ORDER,
            "changed_rows": changed,
        }
    payload["time_window_counts"] = counts
    payload["updated_at_utc"] = now.isoformat()
    _write_json(path, payload)
    return {"path": str(path), "status": "ok", "rows": len(rows), "changed_rows": changed, "counts": counts}


def main() -> int:
    now = datetime.now(UTC)
    local_date = _target_date(now)
    files = [_normalize_file(path, now) for path in _candidate_paths(local_date)]
    payload = {"status": "ok", "created_at_utc": now.isoformat(), "date_local": local_date, "windows": ORDER, "files": files}
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "files": len(files)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
