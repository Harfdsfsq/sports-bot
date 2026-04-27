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
POLICY_VERSION = 'v16-two-hour-primary-five-minute-watchdog'

# Primary workflow slots are at :17 every two hours UTC.
PRIMARY_UTC_HOURS = {0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}
PRIMARY_MINUTE = int(float(os.getenv('AUTORUN_PRIMARY_MINUTE', '17')))
WATCHDOG_DELAY_MINUTES = int(float(os.getenv('AUTORUN_WATCHDOG_DELAY_MINUTES', '5')))
WATCHDOG_MINUTE = (PRIMARY_MINUTE + WATCHDOG_DELAY_MINUTES) % 60
WATCHDOG_WINDOW_MINUTES = max(3, int(float(os.getenv('AUTORUN_WATCHDOG_WINDOW_MINUTES', '10'))))

# If the current primary slot has already succeeded, watchdog skips.
# If only the previous two-hour slot succeeded, the age is ~120+ minutes, so watchdog runs.
RECENT_SUCCESS_GRACE_MINUTES = int(float(os.getenv('AUTORUN_WATCHDOG_RECENT_SUCCESS_MINUTES', '90')))


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
    if now_utc.hour not in PRIMARY_UTC_HOURS:
        return False
    upper = WATCHDOG_MINUTE + WATCHDOG_WINDOW_MINUTES
    return WATCHDOG_MINUTE <= now_utc.minute <= upper


def is_primary_slot(now_utc: datetime) -> bool:
    if now_utc.hour not in PRIMARY_UTC_HOURS:
        return False
    return PRIMARY_MINUTE <= now_utc.minute <= PRIMARY_MINUTE + 15


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
    primary = event == 'schedule' and is_primary_slot(now_utc)
    age = state_age_minutes(state, now_utc)
    skip_main = False
    reason = 'non_schedule_event'

    if event == 'schedule':
        if watchdog:
            if age is not None and age <= RECENT_SUCCESS_GRACE_MINUTES:
                skip_main = True
                reason = f'watchdog_skip_current_slot_success_{age:.1f}m'
            else:
                skip_main = False
                reason = 'watchdog_failsafe_run_missing_current_slot_success'
        elif primary:
            skip_main = False
            reason = 'primary_scheduled_run'
        else:
            skip_main = False
            reason = 'scheduled_run_outside_known_slot_run_anyway'

    env = {
        'AUTORUN_POLICY_VERSION': POLICY_VERSION,
        'AUTORUN_UTC_NOW': now_utc.isoformat(),
        'AUTORUN_MSK_NOW': now_msk.isoformat(),
        'AUTORUN_IS_PRIMARY_SLOT': str(primary).lower(),
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
        'last_is_primary_slot': primary,
        'last_is_watchdog_slot': watchdog,
        'last_skip_main': skip_main,
        'last_decision_reason': reason,
        'last_success_age_minutes': age,
        'recent_success_grace_minutes': RECENT_SUCCESS_GRACE_MINUTES,
        'primary_utc_hours': sorted(PRIMARY_UTC_HOURS),
        'primary_minute': PRIMARY_MINUTE,
        'watchdog_minute': WATCHDOG_MINUTE,
        'watchdog_delay_minutes': WATCHDOG_DELAY_MINUTES,
        'watchdog_window_minutes': WATCHDOG_WINDOW_MINUTES,
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
