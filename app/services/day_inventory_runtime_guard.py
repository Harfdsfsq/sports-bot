from __future__ import annotations

"""Hard runtime guard for day-inventory builds.

The day inventory is useful, but it must never consume the whole run-bot job.
This guard is installed explicitly by day-inventory startup code. It applies
only to scripts/build_day_inventory.py.
"""

import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_day_inventory_runtime_guard_v2_clean_exit"
SUMMARY_PATH = Path(".data/exports/latest-day-inventory-summary.json")


def _is_day_inventory_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else "")
    normalized = argv0.replace("\\", "/")
    return normalized.endswith("scripts/build_day_inventory.py") or normalized.endswith("build_day_inventory.py")


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _write_timeout_summary(timeout_seconds: int) -> None:
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "build_status": "timeout_guarded",
        "status": "skipped_timeout_guard",
        "error": f"day_inventory_timeout_after_{timeout_seconds}s",
        "reason": "day_inventory_build_exceeded_guard_timeout; continuing main run without blocking predictions",
        "bootstrap_provider": os.getenv("DAY_INVENTORY_BOOTSTRAP_PROVIDER") or os.getenv("MATCH_BOOTSTRAP_PROVIDER") or "",
        "force_provider_merge": os.getenv("DAY_INVENTORY_FORCE_PROVIDER_MERGE") or "",
        "timeout_seconds": timeout_seconds,
    }
    try:
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _install_sstats_fixture_source() -> None:
    try:
        from app.services import day_inventory_sstats_fixture_source
        day_inventory_sstats_fixture_source.install()
    except Exception:
        pass


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _is_day_inventory_process():
        return False
    _install_sstats_fixture_source()
    timeout_seconds = max(30, _env_int("DAY_INVENTORY_BUILD_TIMEOUT_SECONDS", 180))

    def _handler(signum, frame):  # type: ignore[no-untyped-def]
        _write_timeout_summary(timeout_seconds)
        print(f"[day-inventory-guard] timeout after {timeout_seconds}s; exiting cleanly so workflow continues", flush=True)
        os._exit(0)

    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout_seconds)
        setattr(sys, PATCH_MARKER, True)
        print(f"[day-inventory-guard] enabled timeout={timeout_seconds}s", flush=True)
        return True
    except Exception:
        return False
