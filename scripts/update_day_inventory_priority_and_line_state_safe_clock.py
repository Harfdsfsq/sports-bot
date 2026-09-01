from __future__ import annotations

"""Run the line-movement planner with a fresh runtime clock.

Some persisted debug artifacts contain an old ``current_time_utc``.  The base
planner intentionally supports simulated clocks, but production runs must not
reuse an old debug timestamp or every near-kickoff candidate looks like it still
has another 2h cron available.  This wrapper forces a fresh clock unless an
explicit simulation flag is enabled.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT = Path('.data/exports/latest-line-guard-clock-source.json')
ART = Path('artifacts/run-bot/latest-line-guard-clock-source.json')
ENV_KEYS = ('HARIZON_RUN_NOW_UTC', 'RUN_NOW_UTC', 'CURRENT_TIME_UTC')


def _parse_dt(value: Any):
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


def _debug_time() -> Any:
    try:
        payload = json.loads(Path('.logs/debug-last-run.json').read_text(encoding='utf-8', errors='replace'))
        if isinstance(payload, dict):
            summary = payload.get('summary') if isinstance(payload.get('summary'), dict) else {}
            return summary.get('current_time_utc') or payload.get('current_time_utc')
    except Exception:
        pass
    return None


def _write(payload: dict[str, Any]) -> None:
    for path in (REPORT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except Exception:
            pass


def main() -> int:
    real_now = datetime.now(UTC)
    explicit = {key: os.getenv(key) for key in ENV_KEYS if os.getenv(key)}
    debug_raw = _debug_time()
    debug_dt = _parse_dt(debug_raw)
    simulate = str(os.getenv('HARIZON_ALLOW_SIMULATED_CLOCK') or '').strip().lower() in {'1', 'true', 'yes', 'on'}

    selected = real_now
    source = 'runtime_now'
    if simulate:
        for key, value in explicit.items():
            dt = _parse_dt(value)
            if dt is not None:
                selected = dt
                source = f'simulated_env:{key}'
                break

    for key in ENV_KEYS:
        os.environ[key] = selected.isoformat()

    payload = {
        'status': 'ok',
        'clock_source': source,
        'selected_now_utc': selected.isoformat(),
        'runtime_now_utc': real_now.isoformat(),
        'debug_time_utc': debug_dt.isoformat() if debug_dt else None,
        'debug_time_age_hours': round(abs((real_now - debug_dt).total_seconds()) / 3600.0, 3) if debug_dt else None,
        'simulation_allowed': simulate,
        'explicit_env_keys_seen': sorted(explicit),
    }
    _write(payload)

    from scripts.update_day_inventory_priority_and_line_state import main as base_main
    return int(base_main() or 0)


if __name__ == '__main__':
    raise SystemExit(main())
