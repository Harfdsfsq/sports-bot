from __future__ import annotations

"""Persist autonomous audit ledgers through the existing run-bot lifecycle.

The workflow only commits flat ``.data/exports/latest-*`` files and the fast
artifact-prune step removes subdirectories and JSONL files.  The original
accumulation module intentionally wrote to
``.data/exports/autonomous-accumulation/*.json[l]``; those files were created
successfully during the run, but were neither committed nor uploaded.

This compatibility layer is installed immediately before
``autonomous_accumulation_runtime``.  It redirects that module's output paths
to flat ``latest-*`` JSON files and replaces append-only JSONL writes with
bounded, atomic JSON-array ledgers.  No workflow change is required.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
LEGACY_OUT = EXPORT / "autonomous-accumulation"

COVERAGE = EXPORT / "latest-autonomous-coverage-matrix.json"
COVERAGE_LEDGER = EXPORT / "latest-autonomous-coverage-run-ledger.json"
PREDICTION_LEDGER = EXPORT / "latest-autonomous-prediction-ledger.json"
LATEST = EXPORT / "latest-autonomous-accumulation-report.json"
POLICY_REPORT = EXPORT / "latest-autonomous-persistence-policy.json"

_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        number = int(float(str(value).strip()))
    except Exception:
        number = default
    return max(minimum, number)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "items", "ledger", "runs"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [dict(row) for row in value if isinstance(row, dict)]
    except Exception:
        pass

    # Migration/repair path for a legacy JSONL file or a partially written file.
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(dict(item))
    except Exception:
        return []
    return rows


def _stable_scalar(value: Any) -> str:
    if isinstance(value, float):
        return "" if not math.isfinite(value) else f"{value:.8g}"
    if isinstance(value, (str, int, bool)) or value is None:
        return str(value or "")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row_key(row: dict[str, Any]) -> str:
    explicit = row.get("row_id") or row.get("candidate_id") or row.get("coverage_id")
    if explicit:
        return str(explicit)
    fields = (
        "run_id",
        "stage",
        "status",
        "match_key",
        "family",
        "selection_key",
        "point",
        "commence_time",
        "created_at_utc",
        "evaluated_at_utc",
    )
    return "|".join(_stable_scalar(row.get(field)) for field in fields)


def _limit_for(path: Path) -> int:
    if path == COVERAGE_LEDGER:
        # Twelve runs/day for seven days plus reserve for manual retries.
        return _as_int(os.getenv("AUTONOMOUS_COVERAGE_LEDGER_MAX_RUNS"), 256)
    return _as_int(os.getenv("AUTONOMOUS_PREDICTION_LEDGER_MAX_ROWS"), 12000)


def _append_bounded_json(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    incoming = [dict(row) for row in rows if isinstance(row, dict)]
    if not incoming:
        return

    # Dict insertion order gives us a compact LRU-like ledger.  Reinsert an
    # updated key at the end so retention trims the oldest untouched row rather
    # than a candidate that was just refreshed by the current run.
    keyed: dict[str, dict[str, Any]] = {}
    for row in [*_load_rows(path), *incoming]:
        key = _row_key(row)
        if key in keyed:
            del keyed[key]
        keyed[key] = row
    ordered = list(keyed.values())

    limit = _limit_for(path)
    if len(ordered) > limit:
        ordered = ordered[-limit:]
    _atomic_json(path, ordered)


def _migrate_legacy(new_path: Path, *legacy_names: str) -> int:
    rows: list[dict[str, Any]] = []
    for name in legacy_names:
        rows.extend(_load_rows(LEGACY_OUT / name))
    if rows:
        _append_bounded_json(new_path, rows)
    return len(rows)


def _write_policy(payload: dict[str, Any]) -> None:
    try:
        _atomic_json(POLICY_REPORT, payload)
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if not _truthy(os.getenv("HARIZON_AUTONOMOUS_ACCUMULATION_MODE"), True):
        return {"status": "disabled_by_env"}
    if _INSTALLED:
        return {"status": "already_installed"}

    try:
        from app.services import autonomous_accumulation_runtime as runtime
    except Exception as exc:
        result = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write_policy(result)
        return result

    runtime.OUT = EXPORT
    runtime.COVERAGE = COVERAGE
    runtime.COVERAGE_LEDGER = COVERAGE_LEDGER
    runtime.PREDICTION_LEDGER = PREDICTION_LEDGER
    runtime.LATEST = LATEST
    runtime._append = _append_bounded_json

    migrated = {
        "coverage_runs": _migrate_legacy(COVERAGE_LEDGER, "coverage-run-ledger.jsonl", "coverage-run-ledger.json"),
        "prediction_rows": _migrate_legacy(PREDICTION_LEDGER, "prediction-ledger.jsonl", "prediction-ledger.json"),
    }
    legacy_coverage = LEGACY_OUT / "latest-coverage-matrix.json"
    legacy_latest = LEGACY_OUT / "latest-accumulation-report.json"
    for source, destination in ((legacy_coverage, COVERAGE), (legacy_latest, LATEST)):
        if source.exists() and source.stat().st_size > 0 and not destination.exists():
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            except Exception:
                pass

    _INSTALLED = True
    result = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "flat_latest_json_bounded_ledgers",
        "workflow_change_required": False,
        "paths": {
            "coverage": str(COVERAGE),
            "coverage_ledger": str(COVERAGE_LEDGER),
            "prediction_ledger": str(PREDICTION_LEDGER),
            "latest_report": str(LATEST),
        },
        "limits": {
            "coverage_runs": _limit_for(COVERAGE_LEDGER),
            "prediction_rows": _limit_for(PREDICTION_LEDGER),
        },
        "migrated": migrated,
        "reason": "run-bot commits only .data/exports/latest-* and prunes subdirectories/jsonl",
    }
    _write_policy(result)
    return result
