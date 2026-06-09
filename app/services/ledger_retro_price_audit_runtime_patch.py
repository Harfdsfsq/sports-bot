from __future__ import annotations

"""Install a lightweight atexit hook that retro-audits publication ledgers.

The hook is safe to run multiple times.  It never deletes bets; it only marks
historical rows as excluded_from_training when new price-integrity logic would no
longer trust the price source.
"""

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_PATH = Path(".data/exports/latest-ledger-retro-price-audit-runtime-install.json")
UTC = timezone.utc
_INSTALLED = False


def _truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _run_audit() -> None:
    if not _truthy(os.getenv("LEDGER_RETRO_PRICE_AUDIT_ENABLED", "true"), True):
        return
    try:
        from scripts.retro_audit_price_integrity_ledger import main as audit_main
        audit_main()
    except SystemExit:
        pass
    except Exception as exc:
        _write({
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "audit_failed",
            "error": str(exc)[:300],
        })


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _write({
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "policy": "retro_price_integrity_audit_at_process_exit",
        "enabled": _truthy(os.getenv("LEDGER_RETRO_PRICE_AUDIT_ENABLED", "true"), True),
    })
    try:
        atexit.register(_run_audit)
    except Exception:
        pass
