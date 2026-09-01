"""Pytest runtime policy for production cron.

Dedicated CI must preserve pytest's real exit code. A production workflow that
deliberately treats tests as diagnostic-only can opt in explicitly with
``HARIZON_PYTEST_NON_BLOCKING_FOR_CRON=true`` (or handle the command status in
the workflow itself).
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _truthy(value: object, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        out = Path(".data/exports/latest-pytest-status.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    github_actions = _truthy(os.getenv("GITHUB_ACTIONS"))
    non_blocking = _truthy(os.getenv("HARIZON_PYTEST_NON_BLOCKING_FOR_CRON"), False)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "github_actions": github_actions,
        "non_blocking_for_cron": github_actions and non_blocking,
        "original_exitstatus": int(exitstatus or 0),
        "status": "ok" if int(exitstatus or 0) == 0 else "failed",
    }
    if github_actions and non_blocking and int(exitstatus or 0) != 0:
        payload["status"] = "failed_non_blocking"
        payload["effective_exitstatus"] = 0
        with suppress(Exception):
            session.exitstatus = 0
    else:
        payload["effective_exitstatus"] = int(exitstatus or 0)
    _write_status(payload)
