from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')


def app_tz() -> ZoneInfo:
    name = os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow'
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def parse_hours(raw: str | None, default: set[int]) -> set[int]:
    if not raw:
        return set(default)
    out: set[int] = set()
    for part in str(raw).replace(';', ',').split(','):
        try:
            hour = int(part.strip())
        except Exception:
            continue
        if 0 <= hour <= 23:
            out.add(hour)
    return out or set(default)


def append_github_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def main() -> int:
    tz = app_tz()
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    target_date = os.getenv('DAY_INVENTORY_TARGET_DATE') or now_local.date().isoformat()
    inventory_path = ROOT / '.data' / 'day_inventory' / f'{target_date}.json'
    inventory = load_json(inventory_path, {})
    counts = inventory.get('counts') if isinstance(inventory.get('counts'), dict) else {}
    matches_total = as_int(counts.get('matches_total'), 0)
    updated_at = parse_dt(inventory.get('updated_at_utc'))
    age_minutes = (now_utc - updated_at).total_seconds() / 60.0 if updated_at is not None else None

    full_hours = parse_hours(os.getenv('DAY_INVENTORY_FULL_BOOTSTRAP_HOURS_LOCAL'), {0})
    refresh_hours = max(1, as_int(os.getenv('DAY_INVENTORY_REFRESH_INTERVAL_HOURS'), 6))
    min_matches_for_skip = max(1, as_int(os.getenv('DAY_INVENTORY_MIN_MATCHES_FOR_SKIP'), 40))
    midnight_fresh_minutes = max(15, as_int(os.getenv('DAY_INVENTORY_MIDNIGHT_FRESH_MINUTES'), 75))

    force_full = str(os.getenv('DAY_INVENTORY_FORCE_FULL_BOOTSTRAP', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    force_refresh = str(os.getenv('DAY_INVENTORY_FORCE_REFRESH', '')).strip().lower() in {'1', 'true', 'yes', 'on'}

    reason = 'skip_existing_inventory'
    skip_build = True
    mode = 'incremental_skip'

    inventory_missing_or_small = not inventory_path.exists() or matches_total < min_matches_for_skip
    inventory_stale = age_minutes is None or age_minutes >= refresh_hours * 60
    full_slot = now_local.hour in full_hours

    if force_full:
        skip_build = False
        mode = 'forced_full_bootstrap'
        reason = 'DAY_INVENTORY_FORCE_FULL_BOOTSTRAP'
    elif full_slot:
        if inventory_missing_or_small or age_minutes is None or age_minutes >= midnight_fresh_minutes:
            skip_build = False
            mode = 'daily_full_bootstrap'
            reason = 'local_midnight_or_configured_full_slot'
        else:
            reason = 'full_slot_inventory_already_fresh'
    elif inventory_missing_or_small:
        skip_build = False
        mode = 'recovery_bootstrap'
        reason = 'inventory_missing_or_below_min_matches'
    elif force_refresh:
        skip_build = False
        mode = 'forced_refresh'
        reason = 'DAY_INVENTORY_FORCE_REFRESH'
    elif inventory_stale:
        skip_build = False
        mode = 'incremental_fixture_refresh'
        reason = f'inventory_age_reached_{refresh_hours}h'

    env = {
        'DAY_INVENTORY_POLICY_ACTIVE': 'true',
        'DAY_INVENTORY_MODE': mode,
        'DAY_INVENTORY_SKIP_BUILD': 'true' if skip_build else 'false',
        'DAY_INVENTORY_TARGET_DATE': target_date,
        'DAY_INVENTORY_BOOTSTRAP_PROVIDER': os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'football_data',
        'DAY_INVENTORY_DIRECT_WINDOW_DAYS': os.getenv('DAY_INVENTORY_DIRECT_WINDOW_DAYS') or '1',
        'DAY_INVENTORY_DIRECT_MIN_MATCHES': os.getenv('DAY_INVENTORY_DIRECT_MIN_MATCHES') or '8',
    }
    append_github_env(env)

    report = {
        'policy_version': 'daily-inventory-api-max-v1',
        'utc_now': now_utc.isoformat(),
        'local_now': now_local.isoformat(),
        'timezone': str(tz.key),
        'target_date': target_date,
        'inventory_path': str(inventory_path),
        'inventory_exists': inventory_path.exists(),
        'matches_total': matches_total,
        'updated_at_utc': updated_at.isoformat() if updated_at is not None else None,
        'age_minutes': round(age_minutes, 2) if age_minutes is not None else None,
        'full_bootstrap_hours_local': sorted(full_hours),
        'refresh_interval_hours': refresh_hours,
        'min_matches_for_skip': min_matches_for_skip,
        'mode': mode,
        'skip_build': skip_build,
        'reason': reason,
        'env_updates': env,
        'notes': [
            '00:00 local run performs the large daily fixture bootstrap unless the inventory is already fresh.',
            'Normal two-hour runs do not repeat the full fixture pull when the daily inventory is fresh.',
            'Odds/context/weather enrichment is handled by the main bot run and then merged back into the inventory.',
        ],
    }
    write_json(EXPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
