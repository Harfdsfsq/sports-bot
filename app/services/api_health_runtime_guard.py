from __future__ import annotations

"""Run a bounded API health probe after provider policy is applied.

The normal run report shows providers that participated in prediction, but some
configured APIs are rotation/probe/optional sources. This guard runs the existing
quick health probe so Telegram reports can show whether those APIs are configured,
reachable, disabled by policy, or failing.
"""

import atexit
import os
import subprocess
import sys
from pathlib import Path

PATCH_MARKER = "_harizon_api_health_runtime_guard_v1"


def _is_provider_budget_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else "").replace("\\", "/")
    return argv0.endswith("scripts/apply_provider_request_budget.py") or argv0.endswith("apply_provider_request_budget.py")


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _run() -> None:
    if not _truthy(os.getenv("API_HEALTH_DURING_RUN_ENABLED"), True):
        return
    script = Path("scripts/api_health_run.py")
    if not script.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--mode", os.getenv("API_HEALTH_MODE", "quick"), "--output-dir", ".data/exports"],
            check=False,
            timeout=int(float(os.getenv("API_HEALTH_GUARD_TIMEOUT_SECONDS", "55") or 55)),
        )
    except Exception as exc:
        print(f"[api-health-guard] skipped after error: {type(exc).__name__}: {exc}", flush=True)


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _is_provider_budget_process():
        return False
    setattr(sys, PATCH_MARKER, True)
    atexit.register(_run)
    print("[api-health-guard] enabled after provider budget policy", flush=True)
    return True
