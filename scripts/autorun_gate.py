from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autorun_state import (
    app_tz,
    latest_slot_key,
    load_state,
    normalize_slot_key,
    prune_old_slots,
    save_state,
    slot_completed,
    slot_record,
    utc_now,
)

UTC = timezone.utc
OUT = Path(".data/exports/latest-autorun-gate.json")
POLICY_VERSION = "v24-slot-supervisor"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_env(env: dict[str, str]) -> None:
    target = os.getenv("GITHUB_ENV")
    lines = [f"{key}={value}" for key, value in sorted(env.items())]
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    else:
        print("\n".join(lines))


def input_value(name: str, default: str = "") -> str:
    for key in (f"AUTORUN_INPUT_{name}", name):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return default


def event_reason(event_name: str) -> str:
    explicit = input_value("RUN_REASON") or input_value("REASON")
    if explicit:
        return explicit
    if event_name == "schedule":
        return "primary"
    if event_name == "workflow_dispatch":
        return "manual"
    return event_name or "unknown"


def current_slot() -> str:
    explicit = input_value("SLOT_KEY")
    if explicit:
        return normalize_slot_key(explicit, app_tz())
    return latest_slot_key(tz=app_tz())


def preflight() -> int:
    now = utc_now()
    local_now = now.astimezone(app_tz())
    event_name = str(os.getenv("GITHUB_EVENT_NAME") or "")
    slot_key = current_slot()
    reason = event_reason(event_name)
    run_id = str(os.getenv("GITHUB_RUN_ID") or "")
    run_attempt = str(os.getenv("GITHUB_RUN_ATTEMPT") or "")

    state = load_state()
    prune_old_slots(state)
    completed = slot_completed(state, slot_key)
    skip = completed
    decision = "skip_slot_already_completed" if completed else "run_slot"

    row = slot_record(state, slot_key)
    if not skip:
        row.update(
            {
                "status": "running",
                "reason": reason,
                "event_name": event_name,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "started_at_utc": now.isoformat(),
                "started_at_local": local_now.isoformat(),
                "policy_version": POLICY_VERSION,
                "missed_slots": input_value("MISSED_SLOTS"),
                "catchup_from": input_value("CATCHUP_FROM"),
                "catchup_to": input_value("CATCHUP_TO"),
            }
        )
    else:
        row.update(
            {
                "last_skipped_duplicate_at_utc": now.isoformat(),
                "last_skipped_duplicate_run_id": run_id,
                "last_skipped_duplicate_reason": reason,
            }
        )

    state.update(
        {
            "timezone": str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"),
            "last_policy_version": POLICY_VERSION,
            "last_gate_at_utc": now.isoformat(),
            "last_gate_at_local": local_now.isoformat(),
            "last_gate_event_name": event_name,
            "last_gate_slot_key": slot_key,
            "last_gate_reason": reason,
            "last_gate_decision": decision,
            "last_gate_skip_main": skip,
            "last_run_id": run_id,
            "last_run_attempt": run_attempt,
        }
    )
    save_state(state)

    env = {
        "AUTORUN_POLICY_VERSION": POLICY_VERSION,
        "AUTORUN_CURRENT_SLOT_KEY": slot_key,
        "AUTORUN_CURRENT_SLOT_LOCAL_KEY": slot_key,
        "AUTORUN_RUN_REASON": reason,
        "AUTORUN_SKIP_MAIN": str(skip).lower(),
        "AUTORUN_DECISION_REASON": decision,
        "AUTORUN_MSK_NOW": local_now.isoformat(),
        "AUTORUN_UTC_NOW": now.isoformat(),
    }
    append_env(env)
    report = {"status": "ok", "env": env, "state": state}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def mark(status: str) -> int:
    now = utc_now()
    local_now = now.astimezone(app_tz())
    slot_key = str(os.getenv("AUTORUN_CURRENT_SLOT_KEY") or current_slot()).strip()
    reason = str(os.getenv("AUTORUN_RUN_REASON") or event_reason(str(os.getenv("GITHUB_EVENT_NAME") or "")) or "").strip()
    run_id = str(os.getenv("GITHUB_RUN_ID") or "")
    run_attempt = str(os.getenv("GITHUB_RUN_ATTEMPT") or "")

    state = load_state()
    row = slot_record(state, slot_key)
    normalized_status = "recovered" if reason in {"watchdog_recovery", "catchup"} and status == "success" else status
    row.update(
        {
            "status": normalized_status,
            "reason": reason,
            "event_name": str(os.getenv("GITHUB_EVENT_NAME") or ""),
            "run_id": run_id,
            "run_attempt": run_attempt,
            "finished_at_utc": now.isoformat(),
            "finished_at_local": local_now.isoformat(),
            "policy_version": POLICY_VERSION,
        }
    )
    if status == "failed":
        row["failure_reason"] = str(os.getenv("AUTORUN_FAILURE_REASON") or "workflow_failed_or_timed_out")
    if normalized_status in {"success", "recovered"}:
        state.update(
            {
                "last_successful_slot_key": slot_key,
                "last_successful_slot_local_key": slot_key,
                "last_successful_scheduled_run_utc": now.isoformat(),
                "last_successful_scheduled_run_msk": local_now.isoformat(),
                "last_successful_run_id": run_id,
                "last_successful_run_attempt": run_attempt,
                "last_successful_trigger_kind": reason,
                "last_success_reason": str(os.getenv("AUTORUN_DECISION_REASON") or ""),
            }
        )
    else:
        state.update(
            {
                "last_failed_slot_key": slot_key,
                "last_failed_at_utc": now.isoformat(),
                "last_failed_run_id": run_id,
                "last_failed_reason": reason,
            }
        )
    state.update({"last_policy_version": POLICY_VERSION, "last_mark_status": normalized_status})
    save_state(state)
    report = {"status": "ok", "mark_status": normalized_status, "slot_key": slot_key, "state": state}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mark-success", action="store_true")
    parser.add_argument("--mark-failed", action="store_true")
    args = parser.parse_args()
    if args.mark_success:
        return mark("success")
    if args.mark_failed:
        return mark("failed")
    return preflight()


if __name__ == "__main__":
    raise SystemExit(main())
