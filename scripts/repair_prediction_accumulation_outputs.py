from __future__ import annotations

"""Repair prediction accumulation outputs after ledger/calibration build.

v7 goal:
- keep prediction-ledger.jsonl for real forecast candidates only;
- remove current-run rows that came only from API coverage diagnostics;
- keep those rows in candidate-opportunity-audit where they belong;
- recalculate ledger summary and calibration counts after repair.

This script does not change model decisions, probabilities, EV, publication guards,
or Telegram picks.  It only cleans analytics artifacts.
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
LEDGER = ROOT / ".data" / "prediction-ledger.jsonl"
SUMMARY = EXPORT_DIR / "latest-prediction-ledger-summary.json"
CALIBRATION = EXPORT_DIR / "latest-prediction-calibration-audit.json"
OUT = EXPORT_DIR / "latest-prediction-accumulation-repair.json"

CORE_METRIC_FIELDS = ("home_team", "away_team", "odds", "ev_pct", "edge_pp")
FORECAST_STAGES = {
    "before_quality",
    "after_quality",
    "value_patch",
    "quality_relief",
    "fallback",
    "normalized_publication",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def as_stage_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(x) for x in value}
    if isinstance(value, tuple):
        return {str(x) for x in value}
    if isinstance(value, dict):
        return {str(k) for k, v in value.items() if bool(v)}
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    return set()


def is_coverage_only(row: dict[str, Any]) -> bool:
    stages = as_stage_set(row.get("stage_seen"))
    if not stages:
        return False
    if stages <= {"api_coverage"}:
        return True
    return not bool(stages & FORECAST_STAGES) and "api_coverage" in stages


def missing_core(row: dict[str, Any]) -> bool:
    return any(row.get(key) in (None, "") for key in CORE_METRIC_FIELDS)


def current_run_id() -> str:
    summary = load_json(SUMMARY, {})
    run_id = str(summary.get("current_run_id") or os.getenv("GITHUB_RUN_ID") or "")
    if run_id:
        return run_id
    calibration = load_json(CALIBRATION, {})
    return str(calibration.get("run_id") or "")


def rebuild_summary(rows: list[dict[str, Any]], run_id: str, removed: int) -> dict[str, Any]:
    by_status = Counter(str(r.get("status") or "unknown") for r in rows)
    by_reason: Counter[str] = Counter()
    for row in rows:
        for reason in row.get("reasons") or []:
            by_reason[str(reason)] += 1
    current = [r for r in rows if str(r.get("run_id") or "") == str(run_id)] if run_id else []
    payload = {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "ledger_path": str(LEDGER),
        "current_run_id": run_id,
        "total_rows": len(rows),
        "current_run_rows": len(current),
        "rows_missing_core_metrics_total": sum(1 for r in rows if missing_core(r)),
        "rows_missing_core_metrics_current_run": sum(1 for r in current if missing_core(r)),
        "by_status": dict(by_status),
        "top_reasons": by_reason.most_common(30),
        "repair_removed_current_run_coverage_only_rows": removed,
        "notes": [
            "Use this ledger for forward testing before trusting ROI.",
            "Published/rejected/watch-only rows are accumulated; settlement fields can be filled by a future results job.",
            "API-coverage-only rows are excluded from prediction-ledger; they remain in candidate-opportunity-audit.",
            "rows_missing_core_metrics_current_run is the main quality signal; total may include older rows produced before ledger fixes.",
        ],
    }
    return payload


def clean_calibration(run_id: str) -> dict[str, Any]:
    payload = load_json(CALIBRATION, {})
    if not isinstance(payload, dict):
        return {"status": "missing"}
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {"status": "no_rows"}

    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        stages = row.get("stage_seen")
        if is_coverage_only({"stage_seen": stages}):
            removed += 1
            continue
        kept.append(row)

    reason_counts: Counter[str] = Counter()
    for row in kept:
        for reason in row.get("reasons") or []:
            reason_counts[str(reason)] += 1

    counts = dict(payload.get("counts") or {})
    counts["candidate_keys"] = len(kept)
    counts["rows_missing_core_metrics"] = sum(
        1 for row in kept
        if any(row.get(field) in (None, "") for field in ("home_team", "away_team", "odds"))
    )
    counts["rows_with_quality"] = sum(1 for row in kept if row.get("quality") is not None)
    counts["coverage_only_rows_removed_by_repair"] = removed

    payload["rows"] = kept
    payload["counts"] = counts
    payload["top_reasons"] = reason_counts.most_common(20)
    payload["repaired_at_utc"] = datetime.now(UTC).isoformat()
    payload["repair_note"] = "Removed api_coverage-only rows from calibration audit; they are opportunity-audit rows, not prediction candidates."
    write_json(CALIBRATION, payload)
    return {"status": "ok", "removed": removed, "remaining": len(kept), "counts": counts}


def main() -> int:
    run_id = current_run_id()
    before_rows = read_jsonl(LEDGER)
    kept: list[dict[str, Any]] = []
    removed_rows: list[dict[str, Any]] = []
    for row in before_rows:
        if run_id and str(row.get("run_id") or "") == str(run_id) and is_coverage_only(row):
            removed_rows.append(row)
        else:
            kept.append(row)

    if removed_rows:
        write_jsonl(LEDGER, kept)

    summary = rebuild_summary(kept, run_id, len(removed_rows))
    write_json(SUMMARY, summary)
    calibration_repair = clean_calibration(run_id)

    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "current_run_id": run_id,
        "ledger_rows_before": len(before_rows),
        "ledger_rows_after": len(kept),
        "coverage_only_rows_removed": len(removed_rows),
        "removed_sample": removed_rows[:10],
        "summary": {
            "current_run_rows": summary.get("current_run_rows"),
            "rows_missing_core_metrics_current_run": summary.get("rows_missing_core_metrics_current_run"),
        },
        "calibration_repair": calibration_repair,
        "notes": [
            "Coverage-only rows are not deleted from candidate-opportunity-audit.",
            "This repair is analytics-only and does not affect publication decisions.",
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
