from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT = ROOT / '.data' / 'exports' / 'latest-next-day-inventory-warmup.json'


def tzinfo() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


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


def should_warm(now_local: datetime, tomorrow_path: Path) -> tuple[bool, str]:
    forced = str(os.getenv('NEXT_DAY_INVENTORY_FORCE', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    if forced:
        return True, 'forced'
    start_hour = as_int(os.getenv('NEXT_DAY_INVENTORY_WARMUP_START_HOUR_LOCAL'), 18)
    if now_local.hour < start_hour:
        return False, f'before_warmup_hour:{now_local.hour}<{start_hour}'
    payload = load_json(tomorrow_path, {})
    counts = payload.get('counts') if isinstance(payload, dict) and isinstance(payload.get('counts'), dict) else {}
    matches_total = as_int(counts.get('matches_total'))
    min_matches = max(1, as_int(os.getenv('NEXT_DAY_INVENTORY_MIN_MATCHES_FOR_SKIP'), 30))
    updated_at = parse_dt(payload.get('updated_at_utc')) if isinstance(payload, dict) else None
    if matches_total < min_matches:
        return True, f'tomorrow_inventory_below_min:{matches_total}<{min_matches}'
    max_age_hours = max(1, as_int(os.getenv('NEXT_DAY_INVENTORY_REFRESH_HOURS'), 6))
    if updated_at is None:
        return True, 'tomorrow_inventory_missing_updated_at'
    age_hours = (datetime.now(UTC) - updated_at).total_seconds() / 3600.0
    if age_hours >= max_age_hours:
        return True, f'tomorrow_inventory_stale:{age_hours:.1f}h>={max_age_hours}h'
    return False, f'tomorrow_inventory_fresh:{matches_total}_matches_age_{age_hours:.1f}h'


def main() -> int:
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tzinfo())
    tomorrow = (now_local.date() + timedelta(days=1)).isoformat()
    tomorrow_path = ROOT / '.data' / 'day_inventory' / f'{tomorrow}.json'
    should_run, reason = should_warm(now_local, tomorrow_path)
    report: dict[str, Any] = {
        'status': 'skipped',
        'updated_at_utc': now_utc.isoformat(),
        'local_now': now_local.isoformat(),
        'target_date': tomorrow,
        'inventory_path': str(tomorrow_path),
        'reason': reason,
    }
    if not should_run:
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    env = dict(os.environ)
    env['DAY_INVENTORY_TARGET_DATE'] = tomorrow
    env['DAY_INVENTORY_BOOTSTRAP_PROVIDER'] = os.getenv('NEXT_DAY_INVENTORY_BOOTSTRAP_PROVIDER') or os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'football_data'
    env['DAY_INVENTORY_DIRECT_WINDOW_DAYS'] = os.getenv('NEXT_DAY_INVENTORY_DIRECT_WINDOW_DAYS') or '1'
    env['DAY_INVENTORY_DIRECT_MIN_MATCHES'] = os.getenv('NEXT_DAY_INVENTORY_DIRECT_MIN_MATCHES') or '8'
    result = subprocess.run([sys.executable, 'scripts/build_day_inventory.py'], cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    built = load_json(tomorrow_path, {})
    counts = built.get('counts') if isinstance(built, dict) and isinstance(built.get('counts'), dict) else {}
    report.update({
        'status': 'ok' if result.returncode == 0 else 'error',
        'returncode': result.returncode,
        'build_output_tail': result.stdout[-3000:],
        'counts': counts,
        'saved_inventory_exists': tomorrow_path.exists(),
        'notes': [
            'This warms tomorrow inventory after evening local time so overnight matches can collect context before kickoff.',
            'It does not change the current run target date or publish quality filters.',
        ],
    })
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
