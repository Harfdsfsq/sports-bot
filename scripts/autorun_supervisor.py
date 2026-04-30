from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from autorun_state import (
    app_tz,
    latest_slot_dt,
    load_state,
    next_slot_dt,
    parse_dt,
    save_state,
    slot_completed,
    slot_pending,
    slot_record,
    utc_now,
)

UTC = timezone.utc
OUT = Path(".data/exports/latest-autorun-supervisor.json")
POLICY_VERSION = "v24-slot-supervisor"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(raw)) if raw not in (None, "") else default
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def slot_from_key(key: str) -> datetime | None:
    parsed = parse_dt(key)
    return parsed.astimezone(app_tz()) if parsed is not None else None


def collect_missed_slots(state: dict[str, Any], current_slot: datetime) -> list[str]:
    max_catchup_slots = max(1, env_int("AUTORUN_MAX_CATCHUP_SLOTS", 6))
    last_success_key = str(state.get("last_successful_slot_key") or "").strip()
    start_slot = slot_from_key(last_success_key) if last_success_key else current_slot
    if start_slot is None:
        start_slot = current_slot

    # If there is no known previous success, recover only the current slot.
    if not last_success_key:
        return [current_slot.isoformat()] if not slot_completed(state, current_slot.isoformat()) else []

    missed: list[str] = []
    cur = next_slot_dt(start_slot)
    while cur <= current_slot and len(missed) < max_catchup_slots:
        key = cur.isoformat()
        if not slot_completed(state, key):
            missed.append(key)
        cur = next_slot_dt(cur)
    if not missed and not slot_completed(state, current_slot.isoformat()):
        missed.append(current_slot.isoformat())
    return missed


def dispatch_workflow(inputs: dict[str, str]) -> dict[str, Any]:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY") or "Harfdsfsq/sports-bot"
    ref_name = os.getenv("GITHUB_REF_NAME") or os.getenv("GITHUB_REF", "main").replace("refs/heads/", "") or "main"
    workflow = os.getenv("AUTORUN_TARGET_WORKFLOW") or "run-bot.yml"
    if not token:
        return {"status": "error", "reason": "github_token_missing"}

    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    body = json.dumps({"ref": ref_name, "inputs": inputs}).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "sports-bot-autorun-supervisor",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            return {"status": "ok", "http_status": response.status, "workflow": workflow, "ref": ref_name, "inputs": inputs}
    except error.HTTPError as exc:
        body_preview = exc.read().decode("utf-8", "replace")[:2000]
        return {"status": "error", "http_status": exc.code, "body": body_preview, "workflow": workflow, "ref": ref_name, "inputs": inputs}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}", "workflow": workflow, "ref": ref_name, "inputs": inputs}


def main() -> int:
    now = utc_now()
    tz = app_tz()
    local_now = now.astimezone(tz)
    current = latest_slot_dt(now, tz)
    current_key = current.isoformat()
    delay_minutes = max(1, env_int("AUTORUN_SUPERVISOR_DELAY_MINUTES", 10))
    pending_ttl = max(15, env_int("AUTORUN_PENDING_TTL_MINUTES", 90))
    elapsed = (local_now - current).total_seconds() / 60.0

    state = load_state()
    report: dict[str, Any] = {
        "status": "noop",
        "policy_version": POLICY_VERSION,
        "checked_at_utc": now.isoformat(),
        "checked_at_local": local_now.isoformat(),
        "current_slot_key": current_key,
        "slot_elapsed_minutes": round(elapsed, 1),
        "delay_minutes": delay_minutes,
    }

    if elapsed < delay_minutes and not env_bool("AUTORUN_SUPERVISOR_FORCE", False):
        report["reason"] = f"slot_delay_not_reached:{elapsed:.1f}<{delay_minutes}"
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if slot_completed(state, current_key):
        report["reason"] = "current_slot_already_completed"
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if slot_pending(state, current_key, now=now, ttl_minutes=pending_ttl):
        report["reason"] = "current_slot_already_running_or_dispatched"
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    missed = collect_missed_slots(state, current)
    if not missed:
        missed = [current_key]

    run_reason = "catchup" if len(missed) > 1 else "watchdog_recovery"
    target_slot = missed[-1]
    inputs = {
        "slot_key": target_slot,
        "run_reason": run_reason,
        "missed_slots": ",".join(missed),
        "catchup_from": missed[0],
        "catchup_to": target_slot,
        "profile": os.getenv("AUTORUN_RECOVERY_PROFILE") or "balanced",
        "publish_window_hours": os.getenv("AUTORUN_RECOVERY_PUBLISH_WINDOW_HOURS") or "9",
        "min_kickoff_lead_minutes": os.getenv("AUTORUN_RECOVERY_MIN_KICKOFF_LEAD_MINUTES") or "25",
        "send_detailed_report": os.getenv("AUTORUN_RECOVERY_SEND_DETAILED_REPORT") or "true",
        "volume_mode": os.getenv("AUTORUN_RECOVERY_VOLUME_MODE") or "target_5",
    }
    dispatch = dispatch_workflow(inputs)

    row = slot_record(state, target_slot)
    if dispatch.get("status") == "ok":
        row.update(
            {
                "status": "dispatched",
                "reason": run_reason,
                "dispatched_at_utc": now.isoformat(),
                "dispatched_at_local": local_now.isoformat(),
                "missed_slots": missed,
                "policy_version": POLICY_VERSION,
            }
        )
    else:
        row.update(
            {
                "status": "dispatch_failed",
                "reason": run_reason,
                "dispatch_failed_at_utc": now.isoformat(),
                "dispatch_error": dispatch,
                "missed_slots": missed,
                "policy_version": POLICY_VERSION,
            }
        )
    state.update(
        {
            "last_supervisor_check_utc": now.isoformat(),
            "last_supervisor_current_slot_key": current_key,
            "last_supervisor_action": dispatch.get("status"),
            "last_supervisor_reason": run_reason,
            "last_supervisor_missed_slots": missed,
            "last_policy_version": POLICY_VERSION,
        }
    )
    save_state(state)

    report.update(
        {
            "status": "dispatched" if dispatch.get("status") == "ok" else "dispatch_failed",
            "reason": run_reason,
            "target_slot_key": target_slot,
            "missed_slots": missed,
            "dispatch": dispatch,
        }
    )
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if dispatch.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
