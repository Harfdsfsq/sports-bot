#!/usr/bin/env python3
"""Apply schedule-aware runtime settings for sports-bot autoruns.

The script writes a compact set of environment overrides to GITHUB_ENV and also
stores a JSON trace in .data/exports/latest-autoran-policy.json. It deliberately
does not weaken model/quality/settlement guardrails; it only controls scan
coverage, minimum kickoff lead, Telegram diagnostic cadence, and per-run caps.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python <3.9 fallback, kept harmless.
    ZoneInfo = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "autoran_policy.json"
API_MAX_OVERRIDES_PATH = ROOT / "config" / "api_max_overrides.env"
REPORT_PATH = ROOT / ".data" / "exports" / "latest-autoran-policy.json"


def _load_policy() -> Dict[str, Any]:
    default: Dict[str, Any] = {
        "timezone": "Europe/Moscow",
        "scheduled": {
            "publish_window_hours": 6,
            "min_kickoff_lead_minutes": 35,
            "max_picks_per_run": 2,
            "send_detailed_report": True,
            "detailed_report_hours_local": [9, 15, 21],
            "detailed_report_min_interval_minutes": 0,
        },
        "workflow_dispatch": {
            "publish_window_hours": 9,
            "min_kickoff_lead_minutes": 25,
            "max_picks_per_run": 2,
            "send_detailed_report": True,
            "detailed_report_min_interval_minutes": 20,
        },
        "push": {
            "publish_window_hours": 6,
            "min_kickoff_lead_minutes": 25,
            "max_picks_per_run": 2,
            "send_detailed_report": False,
            "detailed_report_min_interval_minutes": 1440,
        },
        "always": {
            "prediction_publication_enabled": False,
            "run_report_enabled": False,
            "run_report_only_when_no_predictions": False,
            "controlled_fallback_send_no_pick_report": False,
            "controlled_fallback_use_manual_late_lead": False,
            "detailed_report_send_when_published": True,
            "detailed_report_force_send": True,
            "controlled_fallback_max_picks_per_match": 1,
        },
    }
    if not POLICY_PATH.exists():
        return default
    try:
        loaded = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[autoran] failed to read {POLICY_PATH}: {exc}; using defaults")
        return default
    if not isinstance(loaded, dict):
        return default
    merged = dict(default)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            section = dict(merged[key])
            section.update(value)
            merged[key] = section
        else:
            merged[key] = value
    return merged


def _load_env_file(path: Path) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    if not path.exists():
        return updates
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        print(f"[autoran] failed to read {path}: {exc}")
        return updates
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        updates[key] = value.strip()
    return updates


def _local_now(tz_name: str) -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return max(low, min(high, parsed))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return _parse_int(value, default, low, high)


def _write_github_env(updates: Mapping[str, Any]) -> None:
    env_path = os.environ.get("GITHUB_ENV")
    lines = [f"{key}={value}\n" for key, value in updates.items()]
    if env_path:
        with open(env_path, "a", encoding="utf-8") as fh:
            fh.writelines(lines)
    else:
        for line in lines:
            print(line, end="")


def _pick_section(policy: Mapping[str, Any], event_name: str) -> tuple[str, Mapping[str, Any]]:
    if event_name == "schedule":
        return "scheduled", policy.get("scheduled", {}) if isinstance(policy.get("scheduled"), dict) else {}
    if event_name == "workflow_dispatch":
        return "workflow_dispatch", policy.get("workflow_dispatch", {}) if isinstance(policy.get("workflow_dispatch"), dict) else {}
    return "push_or_other", policy.get("push", {}) if isinstance(policy.get("push"), dict) else {}


def _scheduled_report_enabled(section: Mapping[str, Any], now_local: datetime) -> bool:
    raw_hours = section.get("detailed_report_hours_local", [9, 15, 21])
    if not isinstance(raw_hours, Iterable) or isinstance(raw_hours, (str, bytes)):
        raw_hours = [9, 15, 21]
    hours = {_parse_int(hour, -1, 0, 23) for hour in raw_hours}
    return now_local.hour in hours


def main() -> int:
    policy = _load_policy()
    tz_name = str(policy.get("timezone") or os.environ.get("APP_TIMEZONE") or "Europe/Moscow")
    now_local = _local_now(tz_name)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip() or "unknown"
    mode, section = _pick_section(policy, event_name)
    always = policy.get("always", {}) if isinstance(policy.get("always"), dict) else {}

    default_window = _parse_int(section.get("publish_window_hours", 6), 6, 2, 24)
    default_lead = _parse_int(section.get("min_kickoff_lead_minutes", 35), 35, 5, 180)
    default_max_picks = _parse_int(section.get("max_picks_per_run", 2), 2, 1, 10)

    if event_name == "workflow_dispatch":
        window = _env_int("AUTORUN_MANUAL_PUBLISH_WINDOW_HOURS", default_window, 2, 24)
        lead = _env_int("AUTORUN_MANUAL_MIN_KICKOFF_LEAD_MINUTES", default_lead, 5, 180)
        send_detailed = _parse_bool(
            os.environ.get("AUTORUN_MANUAL_SEND_DETAILED_REPORT"),
            bool(section.get("send_detailed_report", True)),
        )
        detailed_min_interval = _parse_int(section.get("detailed_report_min_interval_minutes", 20), 20, 0, 1440)
    elif event_name == "schedule":
        window = default_window
        lead = default_lead
        if "send_detailed_report" in section:
            send_detailed = bool(section.get("send_detailed_report"))
        else:
            send_detailed = _scheduled_report_enabled(section, now_local)
        detailed_min_interval = _parse_int(section.get("detailed_report_min_interval_minutes", 0), 0, 0, 1440)
    else:
        window = default_window
        lead = default_lead
        send_detailed = bool(section.get("send_detailed_report", False))
        detailed_min_interval = _parse_int(section.get("detailed_report_min_interval_minutes", 1440), 1440, 0, 1440)

    max_picks = default_max_picks
    max_picks_per_match = _parse_int(always.get("controlled_fallback_max_picks_per_match", 1), 1, 1, 5)

    updates: Dict[str, str] = {
        "AUTORUN_POLICY_ACTIVE": "true",
        "AUTORUN_POLICY_MODE": mode,
        "AUTORUN_LOCAL_TIME": now_local.isoformat(),
        "PREDICTION_PUBLICATION_ENABLED": _bool_text(always.get("prediction_publication_enabled", False)),
        "RUN_REPORT_ENABLED": _bool_text(always.get("run_report_enabled", False)),
        "RUN_REPORT_ONLY_WHEN_NO_PREDICTIONS": _bool_text(always.get("run_report_only_when_no_predictions", False)),
        "PUBLISH_WINDOW_HOURS": str(window),
        "MIN_KICKOFF_LEAD_MINUTES": str(lead),
        "MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES": str(lead),
        "MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES": str(lead),
        "CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD": _bool_text(always.get("controlled_fallback_use_manual_late_lead", False)),
        "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT": _bool_text(always.get("controlled_fallback_send_no_pick_report", False)),
        "DETAILED_RUN_REPORT_SEND_TELEGRAM": _bool_text(send_detailed),
        "DETAILED_RUN_REPORT_SEND_WHEN_PUBLISHED": _bool_text(always.get("detailed_report_send_when_published", False)),
        "DETAILED_RUN_REPORT_FORCE_SEND": _bool_text(always.get("detailed_report_force_send", False)),
        "DETAILED_RUN_REPORT_MIN_INTERVAL_MINUTES": str(detailed_min_interval),
        "MAX_PICKS_PER_RUN": str(max_picks),
        "CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN": str(max_picks),
        "CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH": str(max_picks_per_match),
    }
    api_max_updates = _load_env_file(API_MAX_OVERRIDES_PATH)
    updates.update(api_max_updates)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "event_name": event_name,
        "mode": mode,
        "timezone": tz_name,
        "local_time": now_local.isoformat(),
        "publish_window_hours": window,
        "min_kickoff_lead_minutes": lead,
        "max_picks_per_run": max_picks,
        "detailed_report_send_telegram": send_detailed,
        "detailed_report_min_interval_minutes": detailed_min_interval,
        "api_max_overrides_loaded": bool(api_max_updates),
        "api_max_overrides_count": len(api_max_updates),
        "notes": [
            "Autoran policy changes scan cadence/window only; it does not relax model quality guardrails.",
            "Schedule runs use overlapping windows to prevent skipped fixtures when GitHub cron is delayed.",
            "API max overrides enforce targeted API-Football context and strict price/context separation.",
        ],
        "env_updates": updates,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_github_env(updates)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
