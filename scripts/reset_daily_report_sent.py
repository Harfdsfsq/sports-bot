from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc


def _tz() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _target_date() -> str:
    explicit = (os.getenv("DAILY_REPORT_FORCE_DATE") or "").strip()
    if explicit:
        return explicit

    raw_offset = os.getenv("DAILY_REPORT_TARGET_OFFSET_DAYS", "0")
    try:
        offset = max(0, int(raw_offset))
    except Exception:
        offset = 0
    local_now = datetime.now(UTC).astimezone(_tz())
    return (local_now.date() - timedelta(days=offset)).isoformat()


def main() -> int:
    force = str(os.getenv("DAILY_REPORT_FORCE_RESEND", "false")).strip().lower() in {"1", "true", "yes", "y"}
    if not force:
        print("daily report force resend disabled")
        return 0

    state_path = Path(os.getenv("STATE_PATH") or ".data/state.json")
    if not state_path.exists():
        print(f"state not found: {state_path}")
        return 0

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"failed to read state: {type(exc).__name__}: {exc}")
        return 0

    report_date = _target_date()
    reports = state.setdefault("daily_reports", {})
    if isinstance(reports, dict) and report_date in reports:
        reports.pop(report_date, None)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"removed daily report sent marker for {report_date}")
    else:
        print(f"no daily report sent marker for {report_date}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
