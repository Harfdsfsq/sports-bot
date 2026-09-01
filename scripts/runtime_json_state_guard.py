from __future__ import annotations

"""Runtime JSON state guard for HARIZON artifacts.

GitHub Actions restores day-inventory and line-history files from cache and then
commits runtime artifacts back to the repository. If a latest/current alias is
empty, truncated, or contains merge-conflict markers, later scripts silently read
an empty default and the bot loses cumulative inventory/line-movement state.

This guard repairs only alias/runtime files. It never fabricates picks.
"""

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
UTC = timezone.utc
OUT = ROOT / ".data" / "exports" / "latest-runtime-json-state-guard.json"
CONFLICT_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)

DAY_ALIAS_FILES = [
    ROOT / ".data" / "day_inventory" / "current.json",
    ROOT / ".data" / "day_inventory" / "latest.json",
    ROOT / ".data" / "day_inventory" / "today.json",
]
EXPORT_ALIAS_FILES = [ROOT / ".data" / "exports" / "latest-day-inventory-summary.json"]


def _tz() -> ZoneInfo:
    for name in (os.getenv("APP_TIMEZONE"), os.getenv("TZ"), "Europe/Moscow"):
        try:
            return ZoneInfo(str(name))
        except Exception:
            continue
    return ZoneInfo("Europe/Moscow")


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    return explicit or datetime.now(UTC).astimezone(_tz()).date().isoformat()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _json_status(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    status: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size": len(text.encode("utf-8")) if text else 0,
        "valid_json": False,
        "has_conflict_markers": bool(text and CONFLICT_RE.search(text)),
        "empty": not bool(text.strip()),
    }
    if not path.exists():
        status["reason"] = "missing"
        return status
    if status["empty"]:
        status["reason"] = "empty"
        return status
    if status["has_conflict_markers"]:
        status["reason"] = "merge_conflict_markers"
        return status
    try:
        payload = json.loads(text)
        status["valid_json"] = True
        if isinstance(payload, dict):
            rows = payload.get("matches") if isinstance(payload.get("matches"), list) else None
            status["matches_count"] = len(rows) if rows is not None else None
        elif isinstance(payload, list):
            status["rows_count"] = len(payload)
        return status
    except Exception as exc:
        status["reason"] = f"json_parse_error:{type(exc).__name__}:{exc}"
        return status


def _load_json(path: Path) -> Any | None:
    status = _json_status(path)
    if not status.get("valid_json"):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _quarantine(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_suffix(path.suffix + f".corrupt-{stamp}")
    try:
        shutil.copy2(path, target)
        return str(target)
    except Exception:
        return None


def _inventory_candidate_paths(local_date: str) -> list[Path]:
    directory = ROOT / ".data" / "day_inventory"
    preferred = [directory / f"{local_date}.json", directory / "today.json", directory / "latest.json", directory / "current.json"]
    dated = sorted(directory.glob("20??-??-??.json"), reverse=True)
    out: list[Path] = []
    for path in preferred + dated:
        if path not in out:
            out.append(path)
    return out


def _best_inventory_payload(local_date: str, exclude: Path | None = None) -> tuple[Any | None, str | None]:
    best_payload: Any | None = None
    best_path: str | None = None
    best_count = -1
    for path in _inventory_candidate_paths(local_date):
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        payload = _load_json(path)
        if not isinstance(payload, dict):
            continue
        matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        count = len(matches)
        if count > best_count:
            best_payload = payload
            best_path = str(path)
            best_count = count
    return best_payload, best_path


def _repair_day_alias(path: Path, local_date: str) -> dict[str, Any]:
    before = _json_status(path)
    result = {"path": str(path), "before": before, "action": "none"}
    if before.get("valid_json") and not before.get("empty") and not before.get("has_conflict_markers"):
        return result

    quarantine_path = _quarantine(path)
    payload, source = _best_inventory_payload(local_date, exclude=path)
    if isinstance(payload, dict):
        repaired = dict(payload)
        repaired.setdefault("date_local", local_date)
        repaired.setdefault("matches", [])
        repaired.setdefault("sources", {})
        if isinstance(repaired["sources"], dict):
            repaired["sources"]["runtime_json_state_guard"] = {
                "repaired_at_utc": datetime.now(UTC).isoformat(),
                "source": source,
                "repaired_path": str(path),
                "previous_quarantine": quarantine_path,
            }
        repaired["updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_json(path, repaired)
        result.update({"action": "repaired_from_inventory", "source": source, "quarantine": quarantine_path, "after": _json_status(path)})
        return result

    stub = {
        "date_local": local_date,
        "matches": [],
        "status": "empty_runtime_alias_repaired",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "sources": {"runtime_json_state_guard": {"reason": before.get("reason") or "invalid_alias", "previous_quarantine": quarantine_path}},
    }
    _write_json(path, stub)
    result.update({"action": "repaired_to_empty_valid_stub", "quarantine": quarantine_path, "after": _json_status(path)})
    return result


def _repair_export_alias(path: Path, local_date: str) -> dict[str, Any]:
    before = _json_status(path)
    result = {"path": str(path), "before": before, "action": "none"}
    if before.get("valid_json") and not before.get("has_conflict_markers"):
        return result
    quarantine_path = _quarantine(path)
    summary = {
        "status": "repaired_empty_summary",
        "date_local": local_date,
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "reason": before.get("reason") or "invalid_json",
        "previous_quarantine": quarantine_path,
    }
    _write_json(path, summary)
    result.update({"action": "repaired_to_valid_summary_stub", "quarantine": quarantine_path, "after": _json_status(path)})
    return result


def main() -> int:
    local_date = _target_date()
    actions = [_repair_day_alias(path, local_date) for path in DAY_ALIAS_FILES]
    actions.extend(_repair_export_alias(path, local_date) for path in EXPORT_ALIAS_FILES)
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "date_local": local_date,
        "actions": actions,
        "repaired": sum(1 for item in actions if item.get("action") != "none"),
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "repaired": payload["repaired"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
