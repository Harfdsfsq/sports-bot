from __future__ import annotations

"""External-ready autorun supervisor.

Runs from `.github/workflows/autorun-supervisor.yml` and can be triggered by:
- GitHub schedule every 5 minutes;
- workflow_dispatch;
- repository_dispatch from cron-job.org or another external watchdog.

It does not run predictions itself. It checks persistent slot state and dispatches
`run-bot.yml` only when a planned 2-hour slot is missing, failed or stale.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from autorun_state import (
    app_tz,
    iter_slot_keys,
    latest_slot_dt,
    load_state,
    normalize_slot_key,
    parse_dt,
    prune_old_slots,
    save_state,
    slot_completed,
    slot_pending,
    slot_record,
    utc_now,
)

UTC = timezone.utc
OUT = Path(".data/exports/latest-autorun-supervisor.json")
POLICY_VERSION = "v25-external-watchdog-catchup"


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
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def github_token() -> str:
    for name in ("AUTORUN_DISPATCH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def github_repo() -> str:
    return str(os.getenv("GITHUB_REPOSITORY") or "Harfdsfsq/sports-bot").strip() or "Harfdsfsq/sports-bot"


def github_ref() -> str:
    ref = str(os.getenv("AUTORUN_DISPATCH_REF") or os.getenv("GITHUB_REF_NAME") or os.getenv("GITHUB_REF") or "main").strip()
    if ref.startswith("refs/heads/"):
        ref = ref.replace("refs/heads/", "", 1)
    return ref or "main"


def dispatch_workflow(inputs: dict[str, str]) -> dict[str, Any]:
    workflow = str(os.getenv("AUTORUN_TARGET_WORKFLOW") or "run-bot.yml").strip() or "run-bot.yml"
    token = github_token()
    if not token:
        return {"status": "error", "reason": "github_token_missing", "workflow": workflow}

    url = f"https://api.github.com/repos/{github_repo()}/actions/workflows/{workflow}/dispatches"
    body = {"ref": github_ref(), "inputs": inputs}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
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
        with urllib.request.urlopen(req, timeout=20) as response:
            return {"status": "ok", "http_status": int(response.status), "workflow": workflow, "ref": body["ref"], "inputs": inputs}
    except urllib.error.HTTPError as exc:
        try:
            body_preview = exc.read().decode("utf-8", "replace")[:2000]
        except Exception:
            body_preview = ""
        return {"status": "error", "http_status": exc.code, "body": body_preview, "workflow": workflow, "ref": body["ref"], "inputs": inputs}
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}", "workflow": workflow, "ref": body["ref"], "inputs": inputs}


def slot_is_stale(state: dict[str, Any], slot_key: str, *, now: datetime, ttl_minutes: int) -> bool:
    row = (state.get("slots") or {}).get(slot_key)
    if not isinstance(row, dict):
        return False
    status = str(row.get("status") or "").lower()
    if status not in {"running", "dispatched"}:
        return False
    stamp = parse_dt(row.get("started_at_utc") or row.get("dispatched_at_utc"))
    if stamp is None:
        return True
    age = (now.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds() / 60.0
    return age >= ttl_minutes


def slot_status(state: dict[str, Any], slot_key: str) -> str:
    row = (state.get("slots") or {}).get(slot_key)
    if not isinstance(row, dict):
        return "missing"
    return str(row.get("status") or "missing").lower()


def candidate_slots(state: dict[str, Any], *, now: datetime) -> list[dict[str, Any]]:
    tz = app_tz()
    delay_minutes = max(0, env_int("AUTORUN_SUPERVISOR_DELAY_MINUTES", 7))
    pending_ttl = max(15, env_int("AUTORUN_PENDING_TTL_MINUTES", 70))
    max_catchup_slots = max(1, env_int("AUTORUN_MAX_CATCHUP_SLOTS", 4))
    force = env_bool("AUTORUN_SUPERVISOR_FORCE", False)
    external_slot = str(os.getenv("AUTORUN_EXTERNAL_SLOT_KEY") or "").strip()

    if external_slot:
        keys = [normalize_slot_key(external_slot, tz)]
    else:
        current = latest_slot_dt(now, tz)
        end = current
        if (now.astimezone(tz) - current).total_seconds() / 60.0 < delay_minutes and not force:
            end = current - timedelta(hours=2)
        start = end - timedelta(hours=2 * (max_catchup_slots - 1))
        keys = iter_slot_keys(start, end, tz)[-max_catchup_slots:]

    items: list[dict[str, Any]] = []
    for key in keys:
        completed = slot_completed(state, key)
        pending = slot_pending(state, key, now=now, ttl_minutes=pending_ttl)
        stale = slot_is_stale(state, key, now=now, ttl_minutes=pending_ttl)
        status = slot_status(state, key)
        should_dispatch = False
        reason = "slot_completed"
        if force and external_slot:
            should_dispatch = True
            reason = "external_force_slot"
        elif completed:
            should_dispatch = False
            reason = "slot_completed"
        elif pending and not stale:
            should_dispatch = False
            reason = "slot_pending"
        elif status in {"missing", "failed", "error", "dispatch_failed", "cancelled", "timed_out"} or stale or force:
            should_dispatch = True
            reason = "missed_slot" if status == "missing" else "stale_or_failed_slot"
        items.append(
            {
                "slot_key": key,
                "status": status,
                "completed": completed,
                "pending": pending,
                "stale": stale,
                "should_dispatch": should_dispatch,
                "reason": reason,
            }
        )
    return items


def build_inputs(slot_key: str, missed: list[str], reason: str) -> dict[str, str]:
    return {
        "slot_key": slot_key,
        "run_reason": reason,
        "missed_slots": ",".join(missed),
        "catchup_from": missed[0] if missed else slot_key,
        "catchup_to": missed[-1] if missed else slot_key,
        "profile": os.getenv("AUTORUN_RECOVERY_PROFILE") or os.getenv("AUTORUN_SUPERVISOR_PROFILE") or "balanced",
        "publish_window_hours": os.getenv("AUTORUN_RECOVERY_PUBLISH_WINDOW_HOURS") or os.getenv("AUTORUN_SUPERVISOR_PUBLISH_WINDOW_HOURS") or "9",
        "min_kickoff_lead_minutes": os.getenv("AUTORUN_RECOVERY_MIN_KICKOFF_LEAD_MINUTES") or os.getenv("AUTORUN_SUPERVISOR_MIN_KICKOFF_LEAD_MINUTES") or "25",
        "send_detailed_report": os.getenv("AUTORUN_RECOVERY_SEND_DETAILED_REPORT") or "true",
        "volume_mode": os.getenv("AUTORUN_RECOVERY_VOLUME_MODE") or os.getenv("AUTORUN_SUPERVISOR_VOLUME_MODE") or "target_5",
    }


def mark_dispatched(state: dict[str, Any], slot_key: str, *, now: datetime, result: dict[str, Any], missed: list[str], reason: str) -> None:
    row = slot_record(state, slot_key)
    if result.get("status") == "ok":
        row.update(
            {
                "status": "dispatched",
                "reason": reason,
                "dispatched_at_utc": now.isoformat(),
                "dispatched_at_local": now.astimezone(app_tz()).isoformat(),
                "missed_slots": missed,
                "policy_version": POLICY_VERSION,
            }
        )
    else:
        row.update(
            {
                "status": "dispatch_failed",
                "reason": reason,
                "dispatch_failed_at_utc": now.isoformat(),
                "dispatch_error": result,
                "missed_slots": missed,
                "policy_version": POLICY_VERSION,
            }
        )


def main() -> int:
    now = utc_now()
    tz = app_tz()
    dry_run = env_bool("AUTORUN_SUPERVISOR_DRY_RUN", False)
    state = load_state()
    prune_old_slots(state)

    candidates = candidate_slots(state, now=now)
    dispatchable = [item for item in candidates if item.get("should_dispatch")]
    max_dispatches = max(1, env_int("AUTORUN_SUPERVISOR_MAX_DISPATCHES", 1))
    selected = dispatchable[:max_dispatches]
    missed = [str(item.get("slot_key")) for item in selected]
    results: list[dict[str, Any]] = []

    for item in selected:
        slot_key = str(item.get("slot_key"))
        reason = "catchup" if len(missed) > 1 else "watchdog_recovery"
        if str(os.getenv("AUTORUN_SUPERVISOR_MODE") or "").startswith("external"):
            reason = "external_watchdog" if len(missed) <= 1 else "external_catchup"
        inputs = build_inputs(slot_key, missed or [slot_key], reason)
        result = {"status": "ok", "dry_run": True, "workflow": os.getenv("AUTORUN_TARGET_WORKFLOW") or "run-bot.yml", "inputs": inputs} if dry_run else dispatch_workflow(inputs)
        mark_dispatched(state, slot_key, now=now, result=result, missed=missed or [slot_key], reason=reason)
        results.append({"slot_key": slot_key, "candidate": item, "dispatch": result})

    status = "noop"
    reason = "no_missing_slots"
    if selected:
        status = "dispatched" if all(item["dispatch"].get("status") == "ok" for item in results) else "dispatch_failed"
        reason = str(results[-1]["dispatch"].get("status") or status)
    elif candidates:
        reason = str(candidates[-1].get("reason") or "no_missing_slots")

    state.update(
        {
            "last_supervisor_check_utc": now.isoformat(),
            "last_supervisor_check_local": now.astimezone(tz).isoformat(),
            "last_supervisor_policy_version": POLICY_VERSION,
            "last_supervisor_status": status,
            "last_supervisor_reason": reason,
            "last_supervisor_candidates": candidates,
            "last_supervisor_dispatched": [item["slot_key"] for item in results],
        }
    )
    save_state(state)

    report = {
        "status": status,
        "reason": reason,
        "policy_version": POLICY_VERSION,
        "checked_at_utc": now.isoformat(),
        "checked_at_local": now.astimezone(tz).isoformat(),
        "mode": os.getenv("AUTORUN_SUPERVISOR_MODE") or "watchdog",
        "dry_run": dry_run,
        "candidates": candidates,
        "selected": selected,
        "results": results,
        "state_path": ".data/autorun-state.json",
    }
    if selected:
        report["target_slot_key"] = str(selected[-1].get("slot_key"))
        report["missed_slots"] = missed
        report["dispatch"] = results[-1].get("dispatch")
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
