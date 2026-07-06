from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
DAY_DIR = ROOT / ".data" / "day_inventory"
CACHE_DIR = ROOT / ".data" / "cache" / "day_inventory"
EXPORT_DIR = ROOT / ".data" / "exports"
ARTIFACT_DIR = ROOT / "artifacts" / "run-bot"
SNAPSHOT_DIR = ROOT / ".data" / "inventory_guard"
SNAPSHOT_PATH = SNAPSHOT_DIR / "best-day-inventory.json"
REPORT_PATH = EXPORT_DIR / "latest-day-inventory-no-shrink-guard.json"
HIGHWATER_NAMES = ("best-day-inventory-highwater.json", "highwater.json", "largest.json")
CONFLICT_MARKERS = ("<" * 7, "=" * 7, ">" * 7)


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit[:10]
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
        return datetime.now(timezone.utc).astimezone(tz).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in CONFLICT_MARKERS):
            return None
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("matches")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _payload_date(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("date_local", "target_date", "date", "inventory_date"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:10]
    return ""


def _score(payload: dict[str, Any], path: Path, target_date: str) -> tuple[int, int, int, int, str]:
    rows = _rows(payload)
    total = len(rows)
    with_odds = 0
    with_context = 0
    ready = 0
    for row in rows:
        cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        with_odds += int(bool(cov.get("odds") or cov.get("has_odds") or row.get("odds") or row.get("has_odds")))
        with_context += int(bool(cov.get("context") or cov.get("has_context") or row.get("context") or row.get("has_context")))
        ready += int(bool(cov.get("ready_for_model") or row.get("ready_for_model")))
    payload_date = _payload_date(payload)
    date_ok = 1 if not payload_date or payload_date == target_date else 0
    return (date_ok, total, with_odds + with_context + ready, int(path.stat().st_mtime), str(path))


def _highwater_paths(target_date: str) -> list[Path]:
    return [
        SNAPSHOT_PATH,
        *(DAY_DIR / name for name in HIGHWATER_NAMES),
        *(CACHE_DIR / name for name in HIGHWATER_NAMES),
        DAY_DIR / f"{target_date}-highwater.json",
        CACHE_DIR / f"{target_date}-highwater.json",
    ]


def _candidate_paths(target_date: str) -> list[Path]:
    names = [f"{target_date}.json", "current.json", "latest.json", "today.json"]
    paths: list[Path] = []
    for base in (DAY_DIR, CACHE_DIR, ARTIFACT_DIR / "day_inventory"):
        paths.extend(base / name for name in names)
    paths.extend(_highwater_paths(target_date))
    paths.extend([
        EXPORT_DIR / "latest-day-inventory.json",
        EXPORT_DIR / "latest-day-inventory-summary.json",
        EXPORT_DIR / "latest-day-inventory-cumulative-coverage.json",
        EXPORT_DIR / "latest-day-inventory-coverage-truth.json",
        ARTIFACT_DIR / "day_inventory-latest.json",
        ARTIFACT_DIR / "day_inventory-current.json",
        ARTIFACT_DIR / "day_inventory-today.json",
    ])
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _best_payload(target_date: str) -> tuple[Path | None, dict[str, Any] | None, tuple[int, int, int, int, str]]:
    best_path: Path | None = None
    best_payload: dict[str, Any] | None = None
    best_score = (0, 0, 0, 0, "")
    for path in _candidate_paths(target_date):
        payload = _load_json(path)
        if payload is None or not _rows(payload):
            continue
        score = _score(payload, path, target_date)
        if score > best_score:
            best_path = path
            best_payload = payload
            best_score = score
    return best_path, best_payload, best_score


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_highwater(payload: dict[str, Any], target_date: str) -> list[str]:
    if not _rows(payload):
        return []
    payload = dict(payload)
    payload["highwater_updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    changed: list[str] = []
    for path in _highwater_paths(target_date):
        _write_json(path, payload)
        changed.append(str(path))
    return changed


def _copy_payload_to_aliases(payload: dict[str, Any], target_date: str, *, force: bool = False) -> list[str]:
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    incoming_rows = len(_rows(payload))
    if incoming_rows <= 0:
        return changed
    for path in (
        DAY_DIR / f"{target_date}.json",
        DAY_DIR / "current.json",
        DAY_DIR / "latest.json",
        DAY_DIR / "today.json",
        CACHE_DIR / f"{target_date}.json",
        CACHE_DIR / "current.json",
        CACHE_DIR / "latest.json",
        CACHE_DIR / "today.json",
    ):
        old = _load_json(path)
        if not force and len(_rows(old)) >= incoming_rows:
            continue
        _write_json(path, payload)
        changed.append(str(path))
    return changed


def _dedupe_inventory_if_available() -> dict[str, Any]:
    if str(os.getenv("DAY_INVENTORY_SEMANTIC_DEDUPE_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return {"enabled": False, "reason": "disabled_by_env"}
    try:
        from scripts import deduplicate_day_inventory_semantic
        code = int(deduplicate_day_inventory_semantic.main() or 0)
        report = _load_json(EXPORT_DIR / "latest-day-inventory-semantic-dedupe.json") or {}
        return {"enabled": True, "exit_code": code, "report": report}
    except Exception as exc:
        return {"enabled": True, "status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}


def _expand_target_inventory_if_available() -> dict[str, Any]:
    if str(os.getenv("DAY_INVENTORY_NO_SHRINK_RUN_TARGET_EXPAND", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return {"enabled": False, "reason": "disabled_by_env"}
    try:
        from scripts import expand_day_inventory_to_target
        code = expand_day_inventory_to_target.main()
        dedupe = _dedupe_inventory_if_available()
        report = _load_json(EXPORT_DIR / "latest-day-inventory-target-expand.json") or {}
        return {"enabled": True, "exit_code": code, "report": report, "semantic_dedupe": dedupe}
    except Exception as exc:
        return {"enabled": True, "status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}


def snapshot() -> dict[str, Any]:
    target_date = _target_date()
    dedupe_report = _dedupe_inventory_if_available()
    best_path, best_payload, best_score = _best_payload(target_date)
    date_payload = _load_json(DAY_DIR / f"{target_date}.json")
    date_rows = len(_rows(date_payload))
    report: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "snapshot",
        "target_date": target_date,
        "semantic_dedupe": dedupe_report,
        "status": "no_inventory_found",
        "best_path": str(best_path) if best_path else "",
        "best_matches": best_score[1],
        "date_file_matches_before": date_rows,
        "changed_paths": [],
        "highwater_paths": [],
    }
    if best_payload is not None and best_score[1] > 0:
        report["highwater_paths"] = _write_highwater(best_payload, target_date)
        report["status"] = "snapshotted_highwater"
        if best_score[1] > date_rows:
            report["changed_paths"] = _copy_payload_to_aliases(best_payload, target_date, force=True)
            report["status"] = "highwater_snapshot_promoted"
    report["date_file_matches_after"] = len(_rows(_load_json(DAY_DIR / f"{target_date}.json")))
    _write_json(REPORT_PATH, report)
    return report


def repair() -> dict[str, Any]:
    expand_report = _expand_target_inventory_if_available()
    target_date = _target_date()
    current_path, current_payload, current_score = _best_payload(target_date)
    current_rows = current_score[1]
    date_payload = _load_json(DAY_DIR / f"{target_date}.json")
    date_rows = len(_rows(date_payload))
    report: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "repair",
        "target_date": target_date,
        "target_expand_repair": expand_report,
        "target_expand_after_promotion": {"enabled": False, "reason": "disabled_to_prevent_post_promotion_shrink"},
        "current_best_path": str(current_path) if current_path else "",
        "current_best_matches": current_rows,
        "date_file_matches_before": date_rows,
        "status": "ok_no_repair_needed",
        "changed_paths": [],
        "highwater_paths": [],
    }
    changed: list[str] = []
    if current_payload is not None and current_rows > 0:
        report["highwater_paths"] = _write_highwater(current_payload, target_date)
        if current_rows > date_rows:
            changed = _copy_payload_to_aliases(current_payload, target_date, force=True)
            report["status"] = "promoted_current_high_watermark"
            report["changed_paths"] = changed
    else:
        report["status"] = "no_inventory_available"

    post_payload = _load_json(DAY_DIR / f"{target_date}.json")
    post_rows = len(_rows(post_payload))
    if current_payload is not None and current_rows > post_rows:
        extra = _copy_payload_to_aliases(current_payload, target_date, force=True)
        changed.extend([path for path in extra if path not in changed])
        report["changed_paths"] = changed
        report["status"] = "promoted_current_high_watermark"
        post_rows = len(_rows(_load_json(DAY_DIR / f"{target_date}.json")))
    report["date_file_matches_after"] = post_rows
    _write_json(REPORT_PATH, report)
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    mode = (argv[0] if argv else "repair").strip().lower()
    if mode not in {"snapshot", "repair"}:
        print("Usage: guard_day_inventory_no_shrink.py [snapshot|repair]", file=sys.stderr)
        return 2
    payload = snapshot() if mode == "snapshot" else repair()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
