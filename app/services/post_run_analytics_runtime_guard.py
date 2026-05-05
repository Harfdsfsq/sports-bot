from __future__ import annotations

"""Post-run analytics guard.

Runs after selected CLI/reporting commands to keep SQLite runtime storage and the
ML quality cycle fresh without adding fragile workflow YAML steps.
"""

import atexit
import os
import subprocess
import sys
from pathlib import Path

PATCH_MARKER = "_harizon_post_run_analytics_guard_v1"


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _should_install() -> bool:
    argv = " ".join(str(x) for x in sys.argv)
    return "app.cli" in argv or any(cmd in argv for cmd in ("training-dataset", "reporting-sqlite", "run-once"))


def _run_script(path: str, timeout: int) -> None:
    script = Path(path)
    if not script.exists():
        return
    try:
        subprocess.run([sys.executable, str(script)], check=False, timeout=timeout)
    except Exception as exc:
        print(f"[post-run-analytics] {path} failed: {type(exc).__name__}: {exc}", flush=True)


def _run() -> None:
    if _truthy(os.getenv("SQLITE_RUNTIME_STORE_ENABLED"), True):
        _run_script("scripts/sqlite_runtime_store.py", int(float(os.getenv("SQLITE_RUNTIME_STORE_TIMEOUT_SECONDS", "35") or 35)))
    if _truthy(os.getenv("ML_QUALITY_CYCLE_ENABLED"), True):
        _run_script("scripts/ml_quality_cycle.py", int(float(os.getenv("ML_QUALITY_CYCLE_TIMEOUT_SECONDS", "35") or 35)))


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _should_install():
        return False
    setattr(sys, PATCH_MARKER, True)
    atexit.register(_run)
    return True
