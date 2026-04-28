from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
STATE_PATH = ROOT / '.data' / 'autorun-state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-autorun-watchdog.json'
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
POLICY_VERSION = 'v19-msk-midnight-slot-aware'

# Primary workflow slots are at 00 minutes every two hours in MSK.
# Europe/Moscow = UTC+3, so the UTC schedule is:
# 00:00 MSK -> 21:00 UTC previous day, 02:00 MSK -> 23:00 UTC,
# 04:00 MSK -> 01:00 UTC, ... 22:00 MSK -> 19:00 UTC.
DEFAULT_PRIMARY_UTC_HOURS = '21,23,1,3,5,7,9,11,13,15,17,19'
DEFAULT_PRIMARY_UTC_HOURS_SET = {21, 23, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19}
PRIMARY_MINUTE = int(float(os.getenv('AUTORUN_PRIMARY_MINUTE', '0')))
PRIMARY_WINDOW_MINUTES = max(4, int(float(os.getenv('AUTORUN_PRIMARY_WINDOW_MINUTES', '8'))))
DELAYED_PRIMARY_WINDOW_MINUTES = max(PRIMARY_WINDOW_MINUTES, int(float(os.getenv('AUTORUN_DELAYED_PRIMARY_WINDOW_MINUTES', '55'))))
WATCHDOG_DELAY_MINUTES = int(float(os.getenv('AUTORUN_WATCHDOG_DELAY_MINUTES', '5')))
WATCHDOG_WINDOW_MINUTES = max(3, int(float(os.getenv('AUTORUN_WATCHDOG_WINDOW_MINUTES', '8'))))

# Backward-compatible safety net. Slot-aware matching is primary; age is used only as an extra guard.
RECENT_SUCCESS_GRACE_MINUTES = int(float(os.getenv('AUTORUN_WATCHDOG_RECENT_SUCCESS_MINUTES', '90')))


def parse_primary_hours() -> set[int]:
    raw = str(os.getenv('AUTORUN_PRIMARY_UTC_HOURS', DEFAULT_PRIMARY_UTC_HOURS) or DEFAULT_PRIMARY_UTC_HOURS)
    result: set[int] = set()
    for piece in raw.split(','):
        piece = piece.strip()
        if not piece:
            continue
        try:
            hour = int(piece)
        except Exception:
            continue
        if 0 <= hour <= 23:
            result.add(hour)
    return result or set(DEFAULT_PRIMARY_UTC_HOURS_SET)


PRIMARY_UTC_HOURS = parse_primary_hours()
WATCHDOG_MINUTE = (PRIMARY_MINUTE + WATCHDOG_DELAY_MINUTES) % 60


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


def minute_in_window(now_minute: int, start_minute: int, width: int) -> bool:
    return start_minute <= now_minute <= (start_minute + width)


def is_watchdog_slot(now_utc: datetime) -> bool:
    if now_utc.hour not in PRIMARY_UTC_HOURS:
        return False
    return minute_in_window(now_utc.minute, WATCHDOG_MINUTE, WATCHDOG_WINDOW_MINUTES)


def is_primary_slot(now_utc: datetime) -> bool:
    if now_utc.hour not in PRIMARY_UTC_HOURS:
        return False
    return minute_in_window(now_utc.minute, PRIMARY_MINUTE, PRIMARY_WINDOW_MINUTES)


def is_delayed_primary_slot(now_utc: datetime) -> bool:
    if now_utc.hour not in PRIMARY_UTC_HOURS:
        return False
    if now_utc.minute <= PRIMARY_WINDOW_MINUTES:
        return False
    return now_utc.minute <= DELAYED_PRIMARY_WINDOW_MINUTES


def current_slot_key(now_utc: datetime) -> str:
    slot = now_utc.astimezone(UTC).replace(minute=PRIMARY_MINUTE, second=0, microsecond=0)
    return slot.isoformat().replace('+00:00', 'Z')


def current_slot_local_key(now_utc: datetime) -> str:
    slot = now_utc.astimezone(UTC).replace(minute=PRIMARY_MINUTE, second=0, microsecond=0)
    return slot.astimezone(MSK).strftime('%Y-%m-%d %H:%M %Z')


def state_age_minutes(state: dict[str, Any], now_utc: datetime) -> float | None:
    last = parse_dt(state.get('last_successful_scheduled_run_utc'))
    if last is None:
        return None
    return max(0.0, (now_utc - last).total_seconds() / 60.0)


def should_watchdog_skip(state: dict[str, Any], slot_key: str, age: float | None) -> tuple[bool, str]:
    last_slot = str(state.get('last_successful_slot_key') or '').strip()
    if last_slot and last_slot == slot_key:
        return True, 'watchdog_skip_current_slot_success'

    # Backward compatibility for old state files that do not yet contain last_successful_slot_key.
    if not last_slot and age is not None and age <= RECENT_SUCCESS_GRACE_MINUTES:
        return True, f'watchdog_skip_recent_success_legacy_{age:.1f}m'

    if last_slot and last_slot != slot_key:
        return False, 'watchdog_failsafe_run_no_success_for_current_slot'
    return False, 'watchdog_failsafe_run_missing_current_slot_success'


def preflight() -> int:
    now_utc = datetime.now(UTC)
    now_msk = now_utc.astimezone(MSK)
    slot_key = current_slot_key(now_utc)
    slot_local_key = current_slot_local_key(now_utc)
    event = os.getenv('GITHUB_EVENT_NAME', '')
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    watchdog = event == 'schedule' and is_watchdog_slot(now_utc)
    primary = event == 'schedule' and is_primary_slot(now_utc)
    delayed_primary = event == 'schedule' and is_delayed_primary_slot(now_utc)
    age = state_age_minutes(state, now_utc)
    skip_main = False
    reason = 'non_schedule_event'

    if event == 'schedule':
        if watchdog:
            skip_main, reason = should_watchdog_skip(state, slot_key, age)
            if skip_main and age is not None:
                reason = f'{reason}_{age:.1f}m'
        elif primary:
            skip_main = False
            reason = 'primary_scheduled_run'
        elif delayed_primary:
            skip_main = False
            reason = 'delayed_primary_scheduled_run'
        else:
            skip_main = False
            reason = 'scheduled_run_outside_known_slot_run_anyway'

    env = {
        'AUTORUN_POLICY_VERSION': POLICY_VERSION,
        'AUTORUN_UTC_NOW': now_utc.isoformat(),
        'AUTORUN_MSK_NOW': now_msk.isoformat(),
        'AUTORUN_CURRENT_SLOT_KEY': slot_key,
        'AUTORUN_CURRENT_SLOT_LOCAL_KEY': slot_local_key,
        'AUTORUN_IS_PRIMARY_SLOT': str(primary).lower(),
        'AUTORUN_IS_DELAYED_PRIMARY_SLOT': str(delayed_primary).lower(),
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
        'last_current_slot_key': slot_key,
        'last_current_slot_local_key': slot_local_key,
        'last_is_primary_slot': primary,
        'last_is_delayed_primary_slot': delayed_primary,
        'last_is_watchdog_slot': watchdog,
        'last_skip_main': skip_main,
        'last_decision_reason': reason,
        'last_success_age_minutes': age,
        'last_successful_slot_key': state.get('last_successful_slot_key'),
        'recent_success_grace_minutes': RECENT_SUCCESS_GRACE_MINUTES,
        'primary_utc_hours': sorted(PRIMARY_UTC_HOURS),
        'primary_minute': PRIMARY_MINUTE,
        'primary_window_minutes': PRIMARY_WINDOW_MINUTES,
        'delayed_primary_window_minutes': DELAYED_PRIMARY_WINDOW_MINUTES,
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
    slot_key = os.getenv('AUTORUN_CURRENT_SLOT_KEY') or current_slot_key(now_utc)
    slot_local_key = os.getenv('AUTORUN_CURRENT_SLOT_LOCAL_KEY') or current_slot_local_key(now_utc)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.update({
        'last_successful_scheduled_run_utc': now_utc.isoformat(),
        'last_successful_scheduled_run_msk': now_msk.isoformat(),
        'last_successful_slot_key': slot_key,
        'last_successful_slot_local_key': slot_local_key,
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
