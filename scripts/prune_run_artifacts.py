from __future__ import annotations

"""Compact HARIZON runtime artifacts before GitHub upload.

The bot already persists day inventory/line history through cache and commits a
small set of state files.  The GitHub artifact should be a review/debug bundle,
not a full copy of every cache/export tree.  Previous runs produced artifacts
above 1 GB because the workflow uploaded both artifacts/run-bot/** and the full
.data/** trees.  This script removes heavy duplicated payloads and keeps latest
reports that are needed for debugging by Run ID.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
ART = ROOT / "artifacts" / "run-bot"
STATUS = EXPORT / "latest-artifact-prune-status.json"

KEEP_EXPORT_NAMES = {
    "latest-run-bot.log",
    "latest-controlled-fallback-report.json",
    "latest-controlled-fallback-prepublish-guard.json",
    "latest-harizon-telegram-run-report.txt",
    "latest-harizon-telegram-run-report.json",
    "latest-harizon-telegram-run-report-v5.json",
    "latest-harizon-telegram-run-report-v5.txt",
    "latest-harizon-telegram-run-report-v9.json",
    "latest-harizon-telegram-run-report-v9.txt",
    "latest-harizon-telegram-run-report-v10-status.json",
    "latest-publication-status.json",
    "latest-normalized-publication-payloads.json",
    "latest-day-inventory-target-expand.json",
    "latest-day-inventory-coverage-truth.json",
    "latest-day-inventory-coverage-truth.csv",
    "latest-day-inventory-cumulative-coverage.json",
    "latest-inventory-bookmaker-backfill.json",
    "latest-b-cover-candidate-gap-report.json",
    "latest-b-cover-candidate-gap-report.csv",
    "latest-b-cover-value-promotion.json",
    "latest-fresh-b-cover-diagnostics.json",
    "latest-awaiting-movement-candidates.json",
    "latest-provider-smoke.json",
    "latest-provider-smoke.md",
    "latest-artifact-prune-status.json",
    "latest-ab-tier-bookmaker-contract-policy.json",
}

KEEP_STATE_NAMES = {
    "state.json",
    "published-candidate-index.json",
    "fallback-sent-index.json",
    "candidate-lifecycle-state.json",
    "provider_quota_governor_state.json",
    "provider_request_budget_state.json",
    "provider_quota_state.json",
}


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _remove(path: Path, removed: list[dict[str, Any]]) -> None:
    if not path.exists():
        return
    removed.append({"path": str(path), "bytes": _size(path)})
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except OSError:
        pass


def _prune_json_folder(folder: Path, keep_names: set[str], removed: list[dict[str, Any]]) -> None:
    if not folder.exists():
        return
    for child in list(folder.iterdir()):
        if child.name in keep_names:
            continue
        if child.is_dir():
            # Date folders, cache folders and nested copies are the main artifact bloat.
            _remove(child, removed)
        elif child.is_file():
            # Keep only compact latest reports. Remove snapshots/jsonl/heavy dated files.
            if child.name.startswith("latest-") and child.suffix.lower() in {".json", ".txt", ".csv", ".md", ".log"}:
                continue
            _remove(child, removed)


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    before = _size(ART) + _size(EXPORT)
    removed: list[dict[str, Any]] = []

    # Drop duplicated heavy copies under artifacts/run-bot.
    for rel in ("cache", "exports/cache", "exports/line_history"):
        _remove(ART / rel, removed)
    for parent in (ART / "exports", EXPORT):
        _prune_json_folder(parent, KEEP_EXPORT_NAMES, removed)
        if parent.exists():
            for pattern in ("*line-snapshots*.json", "*odds_movement*.json", "*.jsonl", "*.zip"):
                for f in parent.glob(pattern):
                    _remove(f, removed)

    # Keep only current/latest/today/date inventory and line history files in artifact copy.
    cache_date = os.getenv("DAY_INVENTORY_CACHE_DATE") or os.getenv("DAY_INVENTORY_TARGET_DATE") or ""
    keep_day_files = {"current.json", "latest.json", "today.json"}
    if cache_date:
        keep_day_files.add(f"{cache_date}.json")
    for folder in (ART / "day_inventory", ART / "line_history"):
        if folder.exists():
            for f in folder.glob("*.json"):
                if f.name not in keep_day_files:
                    _remove(f, removed)

    # Rebuild a compact review bundle in artifacts/run-bot.
    ART.mkdir(parents=True, exist_ok=True)
    for name in KEEP_EXPORT_NAMES:
        _copy_file(EXPORT / name, ART / name)
    for name in KEEP_STATE_NAMES:
        _copy_file(ROOT / ".data" / name, ART / name)
    for rel in ("day_inventory", "line_history"):
        src_dir = ROOT / ".data" / rel
        dst_dir = ART / rel
        for name in keep_day_files:
            _copy_file(src_dir / name, dst_dir / name)

    after = _size(ART) + _size(EXPORT)
    payload = {
        "status": "ok",
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "bytes_before": before,
        "bytes_after": after,
        "bytes_removed_estimate": max(0, before - after),
        "removed_count": len(removed),
        "removed_sample": removed[:120],
        "keep_export_names": sorted(KEEP_EXPORT_NAMES),
        "notes": [
            "Prunes upload payload only; it does not remove persistent runtime state before cache/save.",
            "Run artifacts are compact latest reports plus selected day_inventory/line_history/state files.",
        ],
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("status", "bytes_before", "bytes_after", "bytes_removed_estimate", "removed_count")}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
