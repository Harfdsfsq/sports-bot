"""Keep strict coverage selection aligned with the configured local-time horizon.

The selector accepts every real fixture within ``RUN_DAYS_AHEAD`` and rejects
identity-only evidence rows that have no teams or exact kickoff. Generated runtime JSON
merge drivers are configured at startup so concurrent scheduled runs cannot persist Git
conflict markers.
"""

from __future__ import annotations

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
        except (TypeError, ValueError):
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
    except (TypeError, ValueError):
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
        return False
    return start <= current < start + timedelta(days=horizon_days())


def _team(row: dict[str, Any], side: str) -> str:
    keys = (
        ("home_team", "home", "home_name", "team_home", "match_home")
        if side == "home"
        else ("away_team", "away", "away_name", "team_away", "match_away")
    )
    return next(
        (
            str(row.get(key) or "").strip()
            for key in keys
            if str(row.get(key) or "").strip()
        ),
        "",
    )


def _real_fixture(row: dict[str, Any], sync_module: Any) -> bool:
    return bool(
        _team(row, "home")
        and _team(row, "away")
        and sync_module.row_kickoff(row) is not None
    )


def _row_quality(row: dict[str, Any], sync_module: Any) -> tuple[int, int, int]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    verified = 0
    for key in (
        "verified_odds_sources",
        "verified_context_sources",
        "verified_bookmakers",
    ):
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set, dict)):
            verified += len(value)
    return (
        int(_real_fixture(row, sync_module)),
        verified,
        len(json.dumps(row, ensure_ascii=False, default=str)),
    )


def _local_identity(row: dict[str, Any], day: str, sync_module: Any) -> Any:
    identity = sync_module._identity(row, day)
    if identity is None:
        identity = sync_module.identity_from_key(sync_module.row_key(row))
    if identity is None:
        return None
    local_day = row_local_day(row, sync_module)
    if local_day is None or not isinstance(identity, tuple) or not identity:
        return identity
    return (local_day.isoformat(), *identity[1:])


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
                if not _real_fixture(row, sync_module):
                    continue
                if not row_in_horizon(row, day, sync_module):
                    continue
                key = _local_identity(row, day, sync_module)
                if key is None:
                    continue
                current = merged.get(key)
                if current is None or _row_quality(row, sync_module) > _row_quality(
                    current, sync_module
                ):
                    merged[key] = row
        return list(merged.values())

    candidate_rows._harizon_local_horizon_inventory = True
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
            [
                "git",
                "config",
                "merge.harizon-runtime-json.name",
                "HARIZON semantic runtime JSON merge",
            ],
            ["git", "config", "merge.harizon-runtime-json.driver", json_command],
            [
                "git",
                "config",
                "merge.harizon-runtime-latest.name",
                "HARIZON latest artifact merge",
            ],
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
        "real_fixture_rows_only": True,
        "horizon_days": horizon_days(),
        "timezone": str(app_timezone()),
        "git_merge_driver": git_driver,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = [
    "app_timezone",
    "horizon_days",
    "install",
    "row_in_horizon",
    "row_local_day",
]
