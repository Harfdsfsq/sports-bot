from __future__ import annotations

"""Defer controlled fallback candidates until the final regular run before kickoff.

HARIZON rule: if a match will still be covered by another regular run before
kickoff, do not publish too early after the second line snapshot.  Example: a
13:30 MSK match should not be sent at 06:00; the last 2-hour run before kickoff
is around 12:00, so publication is allowed there after final checks.

The script is API-free. It only filters fresh candidate artifacts before
publish_controlled_fallback.py and writes a diagnostic report.
"""

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-controlled-fallback-publication-timing-guard.json'
TRUTHY = {'1', 'true', 'yes', 'on', 'y'}


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in TRUTHY


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        return float(raw)
    except Exception:
        return default


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        number = float(str(value).strip().replace(',', '.'))
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return default


def parse_dt(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        # Handle "04.06.2026 16:00 MSK" style snippets if they leak into artifacts.
        m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})', text)
        if m:
            d, mo, y, h, mi = map(int, m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(timezone.utc)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def row_kickoff(row: dict[str, Any], tz: ZoneInfo) -> datetime | None:
    containers = [row]
    for key in ('metadata', 'source_summary', 'market_summary', 'time_guard', 'line_movement'):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    keys = (
        'kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'kickoff_local',
        'start_at', 'match_start', 'event_start', 'event_time', 'starts_at',
    )
    for container in containers:
        for key in keys:
            dt = parse_dt(container.get(key), tz)
            if dt is not None:
                return dt
    return None


def next_regular_run_after(now_local: datetime, interval_hours: int) -> datetime:
    interval_hours = max(1, int(interval_hours or 2))
    base = now_local.replace(minute=0, second=0, microsecond=0)
    # HARIZON regular cadence is every N hours at exact hour boundaries.
    next_hour = ((base.hour // interval_hours) + 1) * interval_hours
    day_add, hour = divmod(next_hour, 24)
    return (base + timedelta(days=day_add)).replace(hour=hour)


def explicit_no_next_regular_run(row: dict[str, Any]) -> bool:
    text = json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    return any(token in text for token in (
        'publish_now_no_next_cron',
        'no_next_regular_run',
        'no_more_regular_run_before_kickoff',
        'нет следующего регулярного run',
    ))


def should_defer(row: dict[str, Any], now_utc: datetime, tz: ZoneInfo) -> tuple[bool, dict[str, Any]]:
    if not env_bool('CONTROLLED_FALLBACK_FINAL_RUN_DEFER_ENABLED', True):
        return False, {'mode': 'disabled'}
    if explicit_no_next_regular_run(row):
        return False, {'mode': 'explicit_no_next_regular_run'}
    kickoff = row_kickoff(row, tz)
    if kickoff is None:
        return False, {'mode': 'kickoff_unknown_allow'}
    now_local = now_utc.astimezone(tz)
    kickoff_local = kickoff.astimezone(tz)
    if kickoff <= now_utc:
        return False, {'mode': 'already_started_or_live_guard_handles'}
    interval = int(env_float('CONTROLLED_FALLBACK_REGULAR_RUN_INTERVAL_HOURS', 2.0))
    min_lead = int(env_float('CONTROLLED_FALLBACK_MIN_KICKOFF_LEAD_MINUTES', 30.0))
    next_run_local = next_regular_run_after(now_local, interval)
    next_run_utc = next_run_local.astimezone(timezone.utc)
    # If the next regular run can still happen with the minimum kickoff lead, wait.
    has_future_regular_check = next_run_utc + timedelta(minutes=min_lead) <= kickoff
    hours_to_kickoff = (kickoff - now_utc).total_seconds() / 3600.0
    return bool(has_future_regular_check), {
        'mode': 'final_regular_run_guard',
        'now_local': now_local.isoformat(),
        'kickoff_local': kickoff_local.isoformat(),
        'next_regular_run_local': next_run_local.isoformat(),
        'interval_hours': interval,
        'min_kickoff_lead_minutes': min_lead,
        'hours_to_kickoff': round(hours_to_kickoff, 3),
        'has_future_regular_check': bool(has_future_regular_check),
    }


def candidate_title(row: dict[str, Any]) -> str:
    home = row.get('home_team') or row.get('home') or ''
    away = row.get('away_team') or row.get('away') or ''
    sel = row.get('selection') or row.get('selection_key') or ''
    odds = row.get('odds') or row.get('selected_odds') or ''
    return f'{home} — {away} | {sel} @{odds}'.strip()


def filter_rows(rows: list[Any], source: str, report: dict[str, Any], now_utc: datetime, tz: ZoneInfo) -> list[Any]:
    kept: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        defer, details = should_defer(row, now_utc, tz)
        if defer:
            report['deferred'].append({
                'source': source,
                'match_key': row.get('match_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'selection': row.get('selection'),
                'point': row.get('point'),
                'odds': row.get('odds') or row.get('selected_odds'),
                'reason': 'publication_timing:wait_for_final_regular_run',
                'details': details,
                'title': candidate_title(row),
            })
            continue
        kept.append(row)
    report['sources'][source] = {'input': len(rows), 'kept': len(kept), 'deferred': len(rows) - len(kept)}
    return kept


def filter_payload_file(path: Path, source: str, report: dict[str, Any], now_utc: datetime, tz: ZoneInfo) -> None:
    payload = load_json(path, None)
    if payload is None:
        return
    changed = False
    if isinstance(payload, dict):
        for key in ('candidates', 'rows', 'items'):
            if isinstance(payload.get(key), list):
                original = payload[key]
                payload[key] = filter_rows(original, f'{source}.{key}', report, now_utc, tz)
                changed = changed or len(payload[key]) != len(original)
        if changed:
            payload['publication_timing_guard_applied'] = True
            payload['publication_timing_guard_deferred'] = sum(v.get('deferred', 0) for k, v in report['sources'].items() if k.startswith(source))
            write_json(path, payload)
    elif isinstance(payload, list):
        filtered = filter_rows(payload, source, report, now_utc, tz)
        if len(filtered) != len(payload):
            write_json(path, filtered)


def filter_debug_file(path: Path, report: dict[str, Any], now_utc: datetime, tz: ZoneInfo) -> None:
    payload = load_json(path, None)
    if not isinstance(payload, dict):
        return
    changed = False
    for key in ('candidates_before_quality', 'candidates_after_quality'):
        if isinstance(payload.get(key), list):
            original = payload[key]
            payload[key] = filter_rows(original, f'debug.{key}', report, now_utc, tz)
            changed = changed or len(payload[key]) != len(original)
    if changed:
        payload['publication_timing_guard_applied'] = True
        write_json(path, payload)


def main() -> int:
    tz = app_tz()
    now_utc = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        'enabled': env_bool('CONTROLLED_FALLBACK_FINAL_RUN_DEFER_ENABLED', True),
        'policy': 'publish_only_on_final_regular_run_before_kickoff',
        'created_at_utc': now_utc.isoformat(),
        'timezone': str(tz),
        'sources': {},
        'deferred': [],
    }
    if not report['enabled']:
        write_json(REPORT_PATH, report)
        return 0
    filter_payload_file(Path('.data/exports/latest-rescue-candidates.json'), 'latest_rescue_candidates', report, now_utc, tz)
    filter_payload_file(Path('artifacts/run-bot/latest-rescue-candidates.json'), 'artifact_rescue_candidates', report, now_utc, tz)
    filter_payload_file(Path('.data/exports/latest-picks.json'), 'latest_picks', report, now_utc, tz)
    filter_debug_file(Path('.logs/debug-last-run.json'), report, now_utc, tz)
    report['deferred_total'] = len(report['deferred'])
    write_json(REPORT_PATH, report)
    if report['deferred']:
        print(f"publication timing guard deferred {len(report['deferred'])} controlled fallback candidates")
    else:
        print('publication timing guard: no early candidates')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
