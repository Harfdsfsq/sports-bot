from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
STATE_PATH = ROOT / '.data' / 'autorun-state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-autorun-watchdog.json'
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
POLICY_VERSION = 'v10-target-volume-watchdog'
WATCHDOG_UTC_HOURS = {4, 10, 16, 22}
RECENT_SUCCESS_GRACE_MINUTES = int(float(os.getenv('AUTORUN_WATCHDOG_RECENT_SUCCESS_MINUTES', '150')))


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def append_env(env: dict[str, str]) -> None:
    target = os.getenv('GITHUB_ENV')
    lines = [f'{key}={value}' for key, value in sorted(env.items())]
    if target:
        with open(target, 'a', encoding='utf-8') as fh:
            for line in lines:
                fh.write(line + '\n')
    else:
        print('\n'.join(lines))


def is_watchdog_slot(now_utc: datetime) -> bool:
    # GitHub schedule can be delayed; treat 40-59 minute executions in watchdog hours as watchdog slots.
    return now_utc.hour in WATCHDOG_UTC_HOURS and now_utc.minute >= 40


def state_age_minutes(state: dict[str, Any], now_utc: datetime) -> float | None:
    last = parse_dt(state.get('last_successful_scheduled_run_utc'))
    if last is None:
        return None
    return max(0.0, (now_utc - last).total_seconds() / 60.0)


def preflight() -> int:
    now_utc = datetime.now(UTC)
    now_msk = now_utc.astimezone(MSK)
    event = os.getenv('GITHUB_EVENT_NAME', '')
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    watchdog = event == 'schedule' and is_watchdog_slot(now_utc)
    age = state_age_minutes(state, now_utc)
    skip_main = False
    reason = 'non_schedule_event'

    if event == 'schedule':
        if watchdog:
            if age is not None and age <= RECENT_SUCCESS_GRACE_MINUTES:
                skip_main = True
                reason = f'watchdog_skip_recent_success_{age:.1f}m'
            else:
                skip_main = False
                reason = 'watchdog_failsafe_run_no_recent_success'
        else:
            skip_main = False
            reason = 'primary_scheduled_run'

    env = {
        'AUTORUN_POLICY_VERSION': POLICY_VERSION,
        'AUTORUN_UTC_NOW': now_utc.isoformat(),
        'AUTORUN_MSK_NOW': now_msk.isoformat(),
        'AUTORUN_IS_WATCHDOG_SLOT': str(watchdog).lower(),
        'AUTORUN_SKIP_MAIN': str(skip_main).lower(),
        'AUTORUN_DECISION_REASON': reason,
        'AUTORUN_LAST_SUCCESS_AGE_MINUTES': '' if age is None else f'{age:.1f}',
    }
    append_env(env)

    state.update({
        'last_preflight_utc': now_utc.isoformat(),
        'last_preflight_msk': now_msk.isoformat(),
        'last_policy_version': POLICY_VERSION,
        'last_event': event,
        'last_is_watchdog_slot': watchdog,
        'last_skip_main': skip_main,
        'last_decision_reason': reason,
        'last_success_age_minutes': age,
        'recent_success_grace_minutes': RECENT_SUCCESS_GRACE_MINUTES,
    })
    write_json(STATE_PATH, state)
    write_json(EXPORT_PATH, state)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def mark_success() -> int:
    now_utc = datetime.now(UTC)
    now_msk = now_utc.astimezone(MSK)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.update({
        'last_successful_scheduled_run_utc': now_utc.isoformat(),
        'last_successful_scheduled_run_msk': now_msk.isoformat(),
        'last_successful_run_id': os.getenv('GITHUB_RUN_ID', ''),
        'last_successful_run_attempt': os.getenv('GITHUB_RUN_ATTEMPT', ''),
        'last_policy_version': POLICY_VERSION,
        'last_success_reason': os.getenv('AUTORUN_DECISION_REASON', ''),
    })
    write_json(STATE_PATH, state)
    write_json(EXPORT_PATH, state)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mark-success', action='store_true')
    args = parser.parse_args()
    if args.mark_success:
        return mark_success()
    return preflight()


if __name__ == '__main__':
    raise SystemExit(main())
