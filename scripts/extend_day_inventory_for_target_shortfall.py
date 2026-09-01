from __future__ import annotations

"""Fill day inventory target shortfalls by temporarily widening the rolling horizon.

At local midnight the new target date can have fewer than 300 known matches even
though the runner is allowed to look ahead.  The base expander respects the normal
RUN_DAYS_AHEAD window; this helper runs after it and, only when the target is still
short, widens the inventory horizon up to a small cap so the 300-row inventory can
be restored from real future fixtures.  Publication guards still use kickoff
windows, value, line movement and source contracts; this only improves the pool.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-day-inventory-shortfall-extend.json'


def _write(payload: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ''):
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _load(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            value = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def main() -> int:
    if not _truthy(os.getenv('DAY_INVENTORY_SHORTFALL_EXTEND_ENABLED'), True):
        payload = {'status': 'disabled', 'created_at_utc': datetime.now(UTC).isoformat()}
        _write(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    from scripts import expand_day_inventory_to_target as expander

    day = expander.target_date()
    target = _as_int(os.getenv('DAY_INVENTORY_TARGET_SIZE') or os.getenv('DAY_INVENTORY_MAX_MATCHES'), 300)
    normal_days = expander.horizon_days()
    max_days = max(normal_days, min(7, _as_int(os.getenv('DAY_INVENTORY_SHORTFALL_EXTEND_MAX_HORIZON_DAYS'), 4)))
    report_before = _load(EXPORT / 'latest-day-inventory-target-expand.json')
    before = _as_int(report_before.get('matches_after'))
    shortfall_before = max(0, target - before)

    payload: dict[str, Any] = {
        'status': 'not_needed',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'target': target,
        'normal_horizon_days': normal_days,
        'max_horizon_days': max_days,
        'before_matches': before,
        'before_shortfall': shortfall_before,
    }

    if target <= 0 or shortfall_before <= 0 or normal_days >= max_days:
        _write(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    previous = os.environ.get('DAY_INVENTORY_HORIZON_DAYS')
    os.environ['DAY_INVENTORY_HORIZON_DAYS'] = str(max_days)
    try:
        exit_code = expander.main()
    finally:
        if previous is None:
            os.environ.pop('DAY_INVENTORY_HORIZON_DAYS', None)
        else:
            os.environ['DAY_INVENTORY_HORIZON_DAYS'] = previous

    report_after = _load(EXPORT / 'latest-day-inventory-target-expand.json')
    after = _as_int(report_after.get('matches_after'))
    payload.update({
        'status': 'extended' if after > before else 'extended_no_gain',
        'expander_exit_code': exit_code,
        'after_matches': after,
        'after_shortfall': max(0, target - after),
        'used_horizon_days': max_days,
        'source_counts': report_after.get('source_counts') if isinstance(report_after.get('source_counts'), dict) else {},
    })
    _write(payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
