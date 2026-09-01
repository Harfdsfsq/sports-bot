from __future__ import annotations

"""Compact HARIZON runtime artifacts before GitHub upload.

The workflow can be close to its job timeout after a heavy provider run.  Pruning
must therefore be bounded and fast; it should never be the reason the artifact is
not uploaded.  In the default fast mode we skip recursive size accounting and the
optional inventory-alias repair, because those can be expensive and the actual
runtime artifacts have already been committed earlier in the job.
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
FAST_PRUNE = str(os.getenv("HARIZON_FAST_ARTIFACT_PRUNE") or "true").strip().lower() in {"1", "true", "yes", "on", "force"}

KEEP_EXPORT_NAMES = {
    "latest-run-bot.log",
    "latest-run-bot-step-status.json",
    "latest-run-bot-error-status.json",
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
    "latest-day-inventory-shortfall-extend.json",
    "latest-day-inventory-blank-row-repair.json",
    "latest-day-inventory-semantic-dedupe.json",
    "latest-day-inventory-coverage-truth.json",
    "latest-day-inventory-coverage-truth.csv",
    "latest-day-inventory-cumulative-coverage.json",
    "latest-inventory-bookmaker-backfill.json",
    "latest-inventory-provider-gap-audit.json",
    "latest-bzzoiro-v2-inventory-target-enrichment.json",
    "latest-bzzoiro-pool-id-inventory-enrichment.json",
    "latest-sstats-deep-inventory-enrichment.json",
    "latest-sstats-crosswalk.json",
    "latest-runbot-discovery-first-prepare.json",
    "latest-runbot-discovery-first-prepare.txt",
    "latest-b-cover-candidate-gap-report.json",
    "latest-b-cover-candidate-gap-report.csv",
    "latest-b-cover-value-promotion.json",
    "latest-fresh-b-cover-diagnostics.json",
    "latest-awaiting-movement-candidates.json",
    "latest-provider-smoke.json",
    "latest-provider-smoke.md",
    "latest-artifact-prune-status.json",
    "latest-all-inventory-json-alias-repair.json",
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
    if FAST_PRUNE:
        return 0
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
            _remove(child, removed)
        elif child.is_file():
            if child.name.startswith("latest-") and child.suffix.lower() in {".json", ".txt", ".csv", ".md", ".log"}:
                continue
            _remove(child, removed)


def _repair_inventory_aliases() -> dict[str, Any]:
    if FAST_PRUNE:
        return {"status": "skipped_fast_prune", "reason": "avoid job timeout before upload-artifact"}
    try:
        from scripts.repair_all_inventory_json_aliases import main as repair_main
        code = int(repair_main() or 0)
        path = EXPORT / "latest-all-inventory-json-alias-repair.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("exit_code", code)
                return payload
        return {"status": "ok", "exit_code": code}
    except Exception as exc:
        return {"status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    repair_report = _repair_inventory_aliases()
    before = _size(ART) + _size(EXPORT)
    removed: list[dict[str, Any]] = []

    for rel in ("cache", "exports", "day_inventory", "line_history"):
        _remove(ART / rel, removed)

    for parent in (EXPORT,):
        _prune_json_folder(parent, KEEP_EXPORT_NAMES, removed)
        if parent.exists():
            for pattern in ("*line-snapshots*.json", "*odds_movement*.json", "*.jsonl", "*.zip"):
                for f in parent.glob(pattern):
                    _remove(f, removed)

    cache_date = os.getenv("DAY_INVENTORY_CACHE_DATE") or os.getenv("DAY_INVENTORY_TARGET_DATE") or ""
    keep_day_files = {"current.json", "latest.json", "today.json"}
    if cache_date:
        keep_day_files.add(f"{cache_date}.json")
    for folder in (ART / "day_inventory", ART / "line_history"):
        if folder.exists():
            for f in folder.glob("*.json"):
                if f.name not in keep_day_files:
                    _remove(f, removed)

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
        "fast_prune": FAST_PRUNE,
        "bytes_before": before,
        "bytes_after": after,
        "bytes_removed_estimate": max(0, before - after),
        "removed_count": len(removed),
        "removed_sample": removed[:120],
        "inventory_alias_repair": repair_report,
        "keep_export_names": sorted(KEEP_EXPORT_NAMES),
        "notes": [
            "Fast prune avoids recursive size accounting/alias repair so upload-artifact still runs before job timeout.",
            "Run artifacts are compact latest reports plus selected day_inventory/line_history/state files.",
        ],
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("status", "fast_prune", "removed_count")}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
