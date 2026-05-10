from __future__ import annotations

import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT = ROOT / '.data' / 'exports' / 'latest-day-inventory-cumulative-coverage.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


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


def source_count(row: dict[str, Any], *names: str) -> int:
    best = 0
    containers = [row]
    for key in ('metadata', 'source_summary', 'market_summary', 'price_summary', 'integrity_report'):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for c in containers:
        for name in names:
            best = max(best, as_int(c.get(name)))
    return best


def bool_cov(row: dict[str, Any], key: str) -> bool:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    return bool(cov.get(key))


def bucket(minutes: float) -> str:
    if minutes < 0:
        return 'started'
    if minutes <= 120:
        return '0_2h'
    if minutes <= 240:
        return '2_4h'
    if minutes <= 360:
        return '4_6h'
    if minutes <= 720:
        return '6_12h'
    return '12h_plus'


def empty_bucket() -> dict[str, Any]:
    return {
        'seen': 0,
        'odds_any': 0,
        'odds_2plus_sources': 0,
        'context_any': 0,
        'context_2plus_sources': 0,
        'ready_for_model': 0,
        'ready_for_publish': 0,
    }


def run_python_script(path: Path) -> dict[str, Any]:
    started = datetime.now(UTC).isoformat()
    try:
        if not path.exists():
            return {'path': str(path), 'status': 'skipped', 'reason': 'missing', 'started_at_utc': started}
        runpy.run_path(str(path), run_name='__main__')
        return {'path': str(path), 'status': 'ok', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat()}
    except SystemExit as exc:
        code = getattr(exc, 'code', 0)
        if code in (0, None):
            return {'path': str(path), 'status': 'ok', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
        return {'path': str(path), 'status': 'error', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
    except Exception as exc:
        return {
            'path': str(path),
            'status': 'error',
            'started_at_utc': started,
            'finished_at_utc': datetime.now(UTC).isoformat(),
            'error': f'{type(exc).__name__}: {exc}',
        }


def ensure_latest_run_coverage_merged() -> list[dict[str, Any]]:
    # The workflow calls only this script after the bot run. Make this script
    # self-contained so the cumulative audit cannot accidentally read stale
    # source counters from the bootstrap inventory.
    steps: list[dict[str, Any]] = []
    steps.append(run_python_script(ROOT / 'scripts' / 'match_data_coverage_report.py'))
    steps.append(run_python_script(ROOT / 'scripts' / 'merge_run_coverage_into_day_inventory.py'))
    # Repair per-match source counters after the run/fallback artifacts exist.
    # This is intentionally after merge_run_coverage_into_day_inventory.py so
    # cumulative coverage sees bookmaker-confirmed price depth and independent
    # context confirmations instead of stale bootstrap counters.
    steps.append(run_python_script(ROOT / 'scripts' / 'repair_inventory_source_counts.py'))
    return steps


def main() -> int:
    pipeline_steps = ensure_latest_run_coverage_merged()
    now = datetime.now(UTC)
    d = target_date()
    inv_path = ROOT / '.data' / 'day_inventory' / f'{d}.json'
    inv = load_json(inv_path, {})
    matches = inv.get('matches') if isinstance(inv.get('matches'), list) else []
    previous = inv.get('coverage_progress') if isinstance(inv.get('coverage_progress'), dict) else {}
    prev_buckets = previous.get('by_kickoff_window') if isinstance(previous.get('by_kickoff_window'), dict) else {}
    min_odds_sources = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context_sources = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    current = {k: empty_bucket() for k in ('0_2h', '2_4h', '4_6h', '6_12h', '12h_plus', 'started')}
    samples_missing: list[dict[str, Any]] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
        if kickoff is None:
            continue
        b = bucket((kickoff - now).total_seconds() / 60.0)
        slot = current.setdefault(b, empty_bucket())
        slot['seen'] += 1
        odds_sources = source_count(
            row,
            'price_confirmation_sources_count',
            'latest_books_max',
            'books_count',
            'odds_sources_count',
            'latest_odds_sources_max',
            'price_sources_count',
            'independent_odds_sources_count',
            'exact_price_sources_count',
            'exact_sources_count',
        )
        context_sources = source_count(
            row,
            'context_sources_count',
            'latest_context_sources_max',
            'confirmation_sources_count',
            'latest_confirmation_sources_max',
        )
        has_odds = bool_cov(row, 'odds') or odds_sources > 0
        has_context = bool_cov(row, 'context') or context_sources > 0
        ready_model = bool_cov(row, 'ready_for_model') or (has_odds and has_context)
        ready_publish = bool_cov(row, 'ready_for_publish') or (odds_sources >= min_odds_sources and context_sources >= min_context_sources)
        slot['odds_any'] += int(has_odds)
        slot['odds_2plus_sources'] += int(odds_sources >= min_odds_sources)
        slot['context_any'] += int(has_context)
        slot['context_2plus_sources'] += int(context_sources >= min_context_sources)
        slot['ready_for_model'] += int(ready_model)
        slot['ready_for_publish'] += int(ready_publish)
        if b != 'started' and len(samples_missing) < 15 and not (odds_sources >= min_odds_sources and context_sources >= min_context_sources):
            samples_missing.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'kickoff_utc': kickoff.isoformat(),
                'league_name': row.get('league_name'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'bucket': b,
                'odds_sources': odds_sources,
                'context_sources': context_sources,
                'has_odds': has_odds,
                'has_context': has_context,
            })
    high_watermark: dict[str, dict[str, Any]] = {}
    for name, data in current.items():
        old = prev_buckets.get(name) if isinstance(prev_buckets.get(name), dict) else {}
        high_watermark[name] = {k: max(as_int(old.get(k)), as_int(data.get(k))) for k in empty_bucket().keys()}
    progress = {
        'updated_at_utc': now.isoformat(),
        'date_local': d,
        'min_odds_sources': min_odds_sources,
        'min_context_sources': min_context_sources,
        'coverage_pipeline_steps': pipeline_steps,
        'current_by_kickoff_window': current,
        'by_kickoff_window': high_watermark,
        'notes': [
            'current_by_kickoff_window is the live rolling window and can shrink when matches start.',
            'by_kickoff_window is the cumulative high watermark and should only grow during the local day.',
            'ready_for_publish requires at least 2 price confirmations and 2 context/confirmation sources by default.',
            'Price confirmations can come from distinct bookmakers/lines even when the API provider is still odds_api_io.',
            'Before calculating cumulative coverage this script rebuilds latest-match-data coverage, merges it into day inventory, and repairs source counters.',
        ],
    }
    inv['coverage_progress'] = progress
    if isinstance(inv.get('counts'), dict):
        inv['counts']['coverage_progress_updated_utc'] = now.isoformat()
        inv['counts']['publish_min_odds_sources'] = min_odds_sources
        inv['counts']['publish_min_context_sources'] = min_context_sources
    inv['updated_at_utc'] = now.isoformat()
    for path in [inv_path, ROOT / '.data' / 'day_inventory' / 'latest.json', ROOT / '.data' / 'day_inventory' / 'current.json', ROOT / '.data' / 'day_inventory' / 'today.json']:
        write_json(path, inv)
    report = {
        'status': 'ok' if matches else 'no_matches',
        'inventory_path': str(inv_path),
        'updated_at_utc': now.isoformat(),
        'date_local': d,
        'matches_total': len(matches),
        'min_odds_sources': min_odds_sources,
        'min_context_sources': min_context_sources,
        'coverage_pipeline_steps': pipeline_steps,
        'current_by_kickoff_window': current,
        'cumulative_high_watermark_by_kickoff_window': high_watermark,
        'missing_2plus_source_examples': samples_missing,
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
