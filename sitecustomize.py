from __future__ import annotations

"""Runtime startup hooks for GitHub Actions.

This file is imported automatically by Python before application modules.  Keep it
small and defensive: failures must never stop the bot.  The important production
hook here is the day-inventory runtime patch; it has to run before
``app.services.runner`` is imported, otherwise the main run keeps using the tiny
publish-window match list instead of the prepared top-300 inventory.
"""

import os
import subprocess
import sys
from pathlib import Path


def _truthy(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_run_once() -> bool:
    argv = " ".join(str(x) for x in sys.argv).lower()
    return "run-once" in argv and ("app.cli" in argv or "cli.py" in argv or "-m" in argv)


def _run_script(path: str) -> None:
    try:
        p = Path(path)
        if p.exists():
            subprocess.run([sys.executable, str(p)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _bootstrap_full_inventory_runtime() -> None:
    if not _truthy("HARIZON_FULL_INVENTORY_COVERAGE_ROUTING_ENABLED", "true"):
        return
    # Must run before app.services.runner is imported.
    _run_script("scripts/apply_day_inventory_runtime_patch.py")
    # Prepare gap counters early as well; no external API calls here.
    _run_script("scripts/maximize_inventory_coverage_runtime.py")


if _is_run_once():
    _bootstrap_full_inventory_runtime()
