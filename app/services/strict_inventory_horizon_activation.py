from __future__ import annotations

"""Keep strict coverage selection aligned with the configured local-time horizon.

The target expander stores the best 300 fixtures across ``RUN_DAYS_AHEAD`` local
calendar days. The original strict synchronizer later filtered those rows by the
UTC-like date embedded in provider keys, shrinking a valid 300-row horizon to the
90 rows whose identities happened to equal the current local date. This installer
replaces only the candidate collection step: evidence ranking and all publication
contracts stay unchanged.

It also configures repository-local merge drivers. Runtime Actions commit artifacts
and then rebase onto ``main``; concurrent runs previously left literal Git conflict
markers in cached inventory aliases because the shell ignored a failed rebase.
"""

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-strict-inventory-horizon-activation.json"
_INSTALLED = False
_ORIGINAL_CANDIDATE_ROWS: Any = None


def _int_env(*names: str, default: int, minimum: int = 1, maximum: int = 4) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(minimum, min(maximum, int(float(str(raw)))))
        except Exception:
            continue
    return max(minimum, min(maximum, default))


def horizon_days() -> int:
    return _int_env(
        "DAY_INVENTORY_HORIZON_DAYS",
        "DAY_INVENTORY_TARGET_HORIZON_DAYS",
        "RUN_DAYS_AHEAD",
        default=2,
    )


def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def row_local_day(row: dict[str, Any], sync_module: Any) -> date | None:
    kickoff = sync_module.row_kickoff(row)
    if kickoff is not None:
        try:
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=UTC)
            return kickoff.astimezone(app_timezone()).date()
        except Exception:
            pass
    identities = sorted(sync_module.row_identities(row, kickoff))
    for identity in identities:
        parsed = _parse_day(identity[0] if identity else None)
        if parsed is not None:
            return parsed
    identity = sync_module.identity_from_key(sync_module.row_key(row))
    return _parse_day(identity[0] if identity else None)


def row_in_horizon(row: dict[str, Any], day: str, sync_module: Any) -> bool:
    start = _parse_day(day)
    current = row_local_day(row, sync_module)
    if start is None:
        return True
    if current is None:
        return True
    return start <= current < start + timedelta(days=horizon_days())


def _candidate_rows_factory(sync_module: Any):
    def candidate_rows(day: str) -> list[dict[str, Any]]:
        merged: dict[Any, dict[str, Any]] = {}
        for path in sync_module._candidate_paths(day):
            payload = sync_module.load(path, {})
            payload_day = (
                str(payload.get("date_local") or payload.get("target_date") or day)[:10]
                if isinstance(payload, dict)
                else ""
            )
            if payload_day and payload_day != day:
                continue
            for row in sync_module._rows(payload):
                if not row_in_horizon(row, day, sync_module):
                    continue
                key = sync_module._identity(row, day)
                if key is None:
                    key = sync_module.identity_from_key(sync_module.row_key(row))
                if key is None:
                    continue
                current = merged.get(key)
                if current is None or len(json.dumps(row, ensure_ascii=False, default=str)) > len(
                    json.dumps(current, ensure_ascii=False, default=str)
                ):
                    merged[key] = row
        return list(merged.values())

    candidate_rows._harizon_local_horizon_inventory = True  # type: ignore[attr-defined]
    return candidate_rows


def _configure_git_merge_driver() -> dict[str, Any]:
    json_script = ROOT / "scripts" / "merge_runtime_json.py"
    latest_script = ROOT / "scripts" / "merge_runtime_latest.py"
    if not json_script.exists() or not latest_script.exists() or not (ROOT / ".git").exists():
        return {"status": "skipped", "reason": "script_or_git_checkout_missing"}
    json_command = "python scripts/merge_runtime_json.py %O %A %B %L %P"
    latest_command = "python scripts/merge_runtime_latest.py %O %A %B %L %P"
    try:
        commands = (
            ["git", "config", "merge.harizon-runtime-json.name", "HARIZON semantic runtime JSON merge"],
            ["git", "config", "merge.harizon-runtime-json.driver", json_command],
            ["git", "config", "merge.harizon-runtime-latest.name", "HARIZON latest artifact merge"],
            ["git", "config", "merge.harizon-runtime-latest.driver", latest_command],
        )
        for command in commands:
            subprocess.run(
                command,
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        return {
            "status": "configured",
            "json_driver": json_command,
            "latest_driver": latest_command,
        }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT.with_suffix(REPORT.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(REPORT)
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_CANDIDATE_ROWS
    from app.services import strict_coverage_inventory_sync as sync_module

    current = sync_module._candidate_rows
    if not getattr(current, "_harizon_local_horizon_inventory", False):
        _ORIGINAL_CANDIDATE_ROWS = current
        sync_module._candidate_rows = _candidate_rows_factory(sync_module)
        candidate_status = "patched"
    else:
        candidate_status = "already_patched"

    git_driver = _configure_git_merge_driver()
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "candidate_rows": candidate_status,
        "horizon_days": horizon_days(),
        "timezone": str(app_timezone()),
        "git_merge_driver": git_driver,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["app_timezone", "horizon_days", "install", "row_in_horizon", "row_local_day"]
