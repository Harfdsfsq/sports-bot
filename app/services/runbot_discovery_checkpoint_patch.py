from __future__ import annotations

"""Preserve the last successful full discovery preparation across incremental runs.

The discovery preparation writes both full and incremental results to the same
``latest-runbot-discovery-first-prepare.json`` file.  Consequently, a successful
incremental run hides the previous full refresh and the following CronJob performs
another expensive full discovery.  This module keeps a separate flat checkpoint
for the last successful full refresh and makes ``previous_full_prepare`` consult it
first.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKPOINT_PATH = Path(".data/exports/latest-runbot-discovery-first-full-prepare.json")
REPORT_PATH = Path(".data/exports/latest-runbot-discovery-checkpoint-policy.json")
ARTIFACT_REPORT_PATH = Path("artifacts/run-bot/latest-runbot-discovery-checkpoint-policy.json")


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _is_successful_full(payload: dict[str, Any]) -> bool:
    mode = str(payload.get("mode") or "").lower()
    status = str(payload.get("status") or "").lower()
    return bool(mode) and "incremental" not in mode and status.startswith("ok")


def _write_report(payload: dict[str, Any]) -> None:
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "checkpoint_path": str(CHECKPOINT_PATH),
        "checkpoint_present": CHECKPOINT_PATH.exists(),
        "checkpoint_mode": str(payload.get("mode") or ""),
        "checkpoint_created_at_utc": payload.get("created_at_utc"),
        "policy": "last_successful_full_prepare_survives_incremental_latest_overwrite",
    }
    for path in (REPORT_PATH, ARTIFACT_REPORT_PATH):
        try:
            _write(path, report)
        except Exception:
            pass


def install_on(target: Any) -> dict[str, Any]:
    """Install on a discovery module; separated for deterministic unit tests."""

    current_previous = getattr(target, "previous_full_prepare", None)
    current_main = getattr(target, "main", None)
    latest_path = Path(getattr(target, "LATEST_JSON_OUT", Path(".data/exports/latest-runbot-discovery-first-prepare.json")))
    if not callable(current_previous) or not callable(current_main):
        return {"status": "target_methods_missing"}
    if getattr(current_previous, "_harizon_full_checkpoint_patched", False):
        checkpoint = _read(CHECKPOINT_PATH)
        _write_report(checkpoint)
        return {"status": "already_patched", "checkpoint_present": bool(checkpoint)}

    # Migration/first deployment: the current latest file may itself be a full run.
    latest = _read(latest_path)
    if _is_successful_full(latest):
        try:
            _write(CHECKPOINT_PATH, latest)
        except Exception:
            pass

    original_previous = current_previous
    original_main = current_main

    def previous_full_prepare_patched(now: datetime | None = None) -> dict[str, Any]:
        checkpoint = _read(CHECKPOINT_PATH)
        checkpoint_result: dict[str, Any] = {}
        if checkpoint:
            original_latest = getattr(target, "LATEST_JSON_OUT", latest_path)
            try:
                target.LATEST_JSON_OUT = CHECKPOINT_PATH
                value = original_previous(now)
                checkpoint_result = value if isinstance(value, dict) else {}
            finally:
                target.LATEST_JSON_OUT = original_latest
            checkpoint_result["source"] = "last_successful_full_checkpoint"
            checkpoint_result["checkpoint_path"] = str(CHECKPOINT_PATH)
            if checkpoint_result.get("reusable"):
                return checkpoint_result

        fallback = original_previous(now)
        fallback = fallback if isinstance(fallback, dict) else {}
        fallback["source"] = "latest_prepare_fallback"
        if checkpoint_result:
            fallback["full_checkpoint"] = checkpoint_result
        return fallback

    previous_full_prepare_patched._harizon_full_checkpoint_patched = True  # type: ignore[attr-defined]

    def main_patched() -> int:
        result = original_main()
        latest_payload = _read(latest_path)
        if _is_successful_full(latest_payload):
            try:
                _write(CHECKPOINT_PATH, latest_payload)
            except Exception:
                pass
        checkpoint_payload = _read(CHECKPOINT_PATH)
        _write_report(checkpoint_payload)
        return int(result or 0)

    main_patched._harizon_full_checkpoint_patched = True  # type: ignore[attr-defined]
    target.previous_full_prepare = previous_full_prepare_patched
    target.main = main_patched

    checkpoint = _read(CHECKPOINT_PATH)
    _write_report(checkpoint)
    return {
        "status": "installed",
        "checkpoint_present": bool(checkpoint),
        "checkpoint_path": str(CHECKPOINT_PATH),
        "latest_path": str(latest_path),
    }


def install() -> dict[str, Any]:
    try:
        from scripts import runbot_discovery_first_prepare as target
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    return install_on(target)


__all__ = ["install", "install_on"]
