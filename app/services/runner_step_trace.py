from __future__ import annotations

"""Lightweight step registry/telemetry for PredictionRunner.

This is the first safe layer for splitting the monolithic runner into explicit
stages without changing runtime behavior. It records the intended architecture
and exposes a small trace file each run so later refactors can move code from
run_once into step classes one stage at a time.
"""

import atexit
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_runner_step_trace_v1"
TRACE_PATH = Path(".data/exports/latest-runner-step-trace.json")
STEPS = ["settlement", "bootstrap", "offers", "context", "candidates", "quality", "export", "publish"]


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_run_once_process() -> bool:
    return any("app.cli" in str(item) or "run-once" == str(item) for item in sys.argv)


def _write_trace(extra: dict[str, Any] | None = None) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "scaffold",
        "steps": [{"name": name, "status": "declared", "migration_state": "inside_prediction_runner_run_once"} for name in STEPS],
        "next_refactor_order": STEPS,
        "notes": [
            "PredictionRunner.run_once is still the behavior source of truth.",
            "This trace declares stable extraction boundaries: settlement, bootstrap, offers, context, candidates, quality, export, publish.",
            "Future commits can move one step at a time behind this registry without changing Telegram/publish behavior.",
        ],
    }
    if extra:
        payload.update(extra)
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRACE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> bool:
    if not _truthy(os.getenv("RUNNER_STEP_TRACE_ENABLED"), True):
        return False
    if getattr(sys, PATCH_MARKER, False):
        return False
    setattr(sys, PATCH_MARKER, True)
    if _is_run_once_process():
        _write_trace({"installed_before_run": True})
        atexit.register(lambda: _write_trace({"installed_before_run": True, "process_finished": True}))
    return True
