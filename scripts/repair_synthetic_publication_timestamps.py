"""Remove publication timestamps synthesized by ledger normalization.

A Telegram send time is transport evidence. ``published_at_utc`` created at ledger-sync
runtime is not. This repair preserves the sent lifecycle and settlement data, but removes
a sync-cluster timestamp when no explicit send timestamp exists.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
BET_DIR = ROOT / ".data" / "bets"
STATE = ROOT / ".data" / "state.json"
SYNC_REPORT = EXPORT / "latest-publication-ledger-sync.json"
OUT = EXPORT / "latest-synthetic-publication-timestamp-repair.json"

JSON_PATHS = (
    EXPORT / "latest-picks.json",
    EXPORT / "latest-bets.json",
    EXPORT / "latest-pending-bets.json",
    EXPORT / "latest-publication-status.json",
    BET_DIR / "pending_bets.json",
    STATE,
)
JSONL_PATHS = (
    BET_DIR / "published_bets.jsonl",
    BET_DIR / "settled_bets.jsonl",
)
_EXPLICIT_SEND_KEYS = (
    "telegram_sent_at_utc",
    "telegram_sent_at",
    "sent_at_utc",
    "sent_at",
)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _explicit_send_time(row: dict[str, Any]) -> datetime | None:
    for key in _EXPLICIT_SEND_KEYS:
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _sent_lifecycle(row: dict[str, Any]) -> bool:
    sent = str(row.get("telegram_sent") or "").strip().lower()
    status = str(
        row.get("publication_lifecycle_status")
        or row.get("publication_lifecycle_stage")
        or row.get("status")
        or ""
    ).strip().lower()
    return sent in {"1", "true", "yes", "on"} or status in {
        "telegram_sent",
        "published",
        "sent",
        "pending",
        "open",
        "active",
    }


def _repair_row(row: dict[str, Any], sync_time: datetime, tolerance: timedelta) -> int:
    if _explicit_send_time(row) is not None or not _sent_lifecycle(row):
        return 0
    removed: list[str] = []
    for key in ("published_at_utc", "published_at"):
        parsed = _parse_time(row.get(key))
        if parsed is None or abs(parsed - sync_time) > tolerance:
            continue
        row.setdefault(f"invalid_{key}", row.get(key))
        row.pop(key, None)
        removed.append(key)
    if not removed:
        return 0
    row["publication_time_missing"] = True
    row["synthetic_publication_timestamp_removed"] = True
    row["synthetic_publication_timestamp_fields"] = removed
    return 1


def _walk(value: Any, sync_time: datetime, tolerance: timedelta) -> tuple[int, int]:
    rows_seen = 0
    repaired = 0
    if isinstance(value, list):
        for item in value:
            seen, changed = _walk(item, sync_time, tolerance)
            rows_seen += seen
            repaired += changed
        return rows_seen, repaired
    if not isinstance(value, dict):
        return 0, 0
    looks_like_row = any(
        key in value
        for key in (
            "match_key",
            "home_team",
            "away_team",
            "selection",
            "telegram_sent",
            "publication_lifecycle_status",
        )
    )
    if looks_like_row:
        rows_seen += 1
        repaired += _repair_row(value, sync_time, tolerance)
    for child in value.values():
        if isinstance(child, (dict, list)):
            seen, changed = _walk(child, sync_time, tolerance)
            rows_seen += seen
            repaired += changed
    return rows_seen, repaired


def _repair_json(path: Path, sync_time: datetime, tolerance: timedelta) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {
            "path": str(path),
            "status": "missing",
            "rows_seen": 0,
            "rows_repaired": 0,
        }
    payload = _load_json(path, None)
    if payload is None:
        return {
            "path": str(path),
            "status": "invalid_json",
            "rows_seen": 0,
            "rows_repaired": 0,
        }
    rows_seen, repaired = _walk(payload, sync_time, tolerance)
    if repaired:
        _write_json(path, payload)
    return {
        "path": str(path),
        "status": "updated" if repaired else "unchanged",
        "rows_seen": rows_seen,
        "rows_repaired": repaired,
    }


def _repair_jsonl(path: Path, sync_time: datetime, tolerance: timedelta) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {
            "path": str(path),
            "status": "missing",
            "rows_seen": 0,
            "rows_repaired": 0,
        }
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            invalid_lines += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
    repaired = sum(_repair_row(row, sync_time, tolerance) for row in rows)
    if repaired:
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
    return {
        "path": str(path),
        "status": "updated" if repaired else "unchanged",
        "rows_seen": len(rows),
        "rows_repaired": repaired,
        "invalid_lines": invalid_lines,
    }


def repair_exports(*, tolerance_minutes: int = 10) -> dict[str, Any]:
    report = _load_json(SYNC_REPORT, {})
    sync_time = _parse_time(
        report.get("created_at_utc") if isinstance(report, dict) else None
    )
    payload: dict[str, Any] = {
        "status": "no_sync_timestamp" if sync_time is None else "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "sync_time_utc": sync_time.isoformat() if sync_time else None,
        "tolerance_minutes": tolerance_minutes,
        "files": [],
        "rows_seen": 0,
        "rows_repaired": 0,
        "send_lifecycle_preserved": True,
        "settlement_preserved": True,
    }
    if sync_time is not None:
        tolerance = timedelta(minutes=max(1, tolerance_minutes))
        for path in JSON_PATHS:
            payload["files"].append(_repair_json(path, sync_time, tolerance))
        for path in JSONL_PATHS:
            payload["files"].append(_repair_jsonl(path, sync_time, tolerance))
        payload["rows_seen"] = sum(
            item.get("rows_seen", 0) for item in payload["files"]
        )
        payload["rows_repaired"] = sum(
            item.get("rows_repaired", 0) for item in payload["files"]
        )
    _write_json(OUT, payload)
    return payload


def main() -> int:
    payload = repair_exports()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
