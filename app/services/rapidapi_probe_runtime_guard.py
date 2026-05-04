from __future__ import annotations

"""Hard runtime guard for legacy RapidAPI probe scripts.

The old rapidapi_quota_probe.py and rapidapi_endpoint_discovery.py are still
called by run-bot after the main prediction run. They are diagnostic-only and
must never block artifacts/reports. This guard exits them after a bounded time
and writes a minimal summary so the workflow can continue.
"""

import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_rapidapi_probe_runtime_guard_v1"
EXPORT_DIR = Path(".data/exports")


def _script_name() -> str:
    return Path(str(sys.argv[0] if sys.argv else "")).name


def _is_target_process() -> bool:
    return _script_name() in {"rapidapi_quota_probe.py", "rapidapi_endpoint_discovery.py"}


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _paths_for_script(script: str) -> list[Path]:
    if script == "rapidapi_quota_probe.py":
        return [
            EXPORT_DIR / "latest-rapidapi-provider-probe.json",
            EXPORT_DIR / "latest-rapidapi-provider-summary.json",
        ]
    if script == "rapidapi_endpoint_discovery.py":
        return [
            EXPORT_DIR / "latest-rapidapi-endpoint-discovery.json",
            EXPORT_DIR / "latest-rapidapi-endpoint-discovery-summary.json",
        ]
    return []


def _write_timeout_summary(timeout_seconds: int) -> None:
    script = _script_name()
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": script,
        "status": "timeout_guarded",
        "reason": f"{script}_exceeded_{timeout_seconds}s; diagnostic-only probe skipped so main run can continue",
        "timeout_seconds": timeout_seconds,
        "called": 0,
        "ok": 0,
        "errors": 0,
        "skipped": 1,
    }
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        for path in _paths_for_script(script):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _is_target_process():
        return False
    timeout_seconds = max(10, _env_int("RAPIDAPI_LEGACY_PROBE_TIMEOUT_SECONDS", 45))

    def _handler(signum, frame):  # type: ignore[no-untyped-def]
        _write_timeout_summary(timeout_seconds)
        print(f"[rapidapi-probe-guard] {_script_name()} timeout after {timeout_seconds}s; continuing workflow", flush=True)
        raise SystemExit(124)

    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout_seconds)
        setattr(sys, PATCH_MARKER, True)
        print(f"[rapidapi-probe-guard] enabled for {_script_name()} timeout={timeout_seconds}s", flush=True)
        return True
    except Exception:
        return False
