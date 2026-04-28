from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-audit.json'


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


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def deep_int(payload: Any, names: set[str], default: int = 0) -> int:
    best = default
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in names:
                    best = max(best, as_int(value, default))
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list)))
    return best


def provider_attempt_stats(inventory: dict[str, Any]) -> dict[str, dict[str, int]]:
    sources = inventory.get('sources') if isinstance(inventory.get('sources'), dict) else {}
    attempts = sources.get('attempts') if isinstance(sources.get('attempts'), dict) else {}
    out: dict[str, dict[str, int]] = {}
    for provider, payload in attempts.items():
        if not isinstance(payload, dict):
            continue
        stats = payload.get('stats') if isinstance(payload.get('stats'), dict) else payload
        if not isinstance(stats, dict):
            continue
        out[str(provider)] = {
            'events_fetched': as_int(stats.get('events_fetched')),
            'matches_built': as_int(stats.get('matches_built')),
            'matches_for_target_local_date': as_int(stats.get('matches_for_target_local_date')),
            'low_tier_skipped': as_int(stats.get('low_tier_skipped')),
            'requests': as_int(stats.get('requests')),
            'response_errors': as_int(stats.get('response_errors')),
        }
    combined = sources.get('stats') if isinstance(sources.get('stats'), dict) else {}
    if combined:
        out['combined_bootstrap'] = {
            'matches_combined_raw': as_int(combined.get('matches_combined_raw')),
            'matches_combined_deduped': as_int(combined.get('matches_combined_deduped')),
            'errors': len(combined.get('errors') or []) if isinstance(combined.get('errors'), list) else 0,
        }
    return out


def match_window_flags(row: dict[str, Any], now: datetime) -> dict[str, bool]:
    kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
    if kickoff is None:
        return {'future': False, 'soon': False, 'late': False}
    minutes = (kickoff - now).total_seconds() / 60.0
    return {
        'future': minutes > 0,
        'soon': 0 < minutes <= 360,
        'late': minutes <= 0,
    }


def missing_context_examples(matches: list[dict[str, Any]], now: datetime, limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        if bool(coverage.get('context')):
            continue
        kickoff = parse_dt(row.get('kickoff_utc') or row.get('kickoff_local'))
        if kickoff is None or kickoff <= now:
            continue
        rows.append({
            'match_key': row.get('match_key') or row.get('canonical_match_id'),
            'kickoff_utc': kickoff.isoformat(),
            'kickoff_local': row.get('kickoff_local'),
            'league_name': row.get('league_name'),
            'home_team': row.get('home_team'),
            'away_team': row.get('away_team'),
            'has_odds': bool(coverage.get('odds')),
            'minutes_to_kickoff': round((kickoff - now).total_seconds() / 60.0, 1),
        })
    rows.sort(key=lambda item: (0 if item.get('has_odds') else 1, item.get('minutes_to_kickoff') or 999999))
    return rows[:limit]


def run_script(script_name: str, export_path: str | None = None) -> dict[str, Any]:
    path = ROOT / 'scripts' / script_name
    if not path.exists():
        return {'status': 'skipped', 'reason': 'script_missing', 'script': script_name}
    result = subprocess.run([sys.executable, str(path)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    payload = load_json(ROOT / export_path, {}) if export_path else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        'status': 'ok' if result.returncode == 0 else 'error',
        'returncode': result.returncode,
        'report': payload,
        'output_tail': result.stdout[-1500:],
    }


def run_next_day_warmup() -> dict[str, Any]:
    return run_script('warm_next_day_inventory.py', '.data/exports/latest-next-day-inventory-warmup.json')


def run_near_miss_queue_build() -> dict[str, Any]:
    return run_script('build_near_miss_enrichment_queue.py', '.data/exports/latest-near-miss-enrichment-queue.json')


def main() -> int:
    now = datetime.now(UTC)
    local_date = target_date()
    inventory_path = ROOT / '.data' / 'day_inventory' / f'{local_date}.json'
    inventory = load_json(inventory_path, {})
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    merge = load_json(ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-merge.json', {})
    policy = load_json(ROOT / '.data' / 'exports' / 'latest-day-inventory-policy.json', {})

    matches = inventory.get('matches') if isinstance(inventory.get('matches'), list) else []
    counts = inventory.get('counts') if isinstance(inventory.get('counts'), dict) else {}
    provider_stats = provider_attempt_stats(inventory if isinstance(inventory, dict) else {})

    future = 0
    soon = 0
    late = 0
    with_odds = 0
    with_context = 0
    ready_model = 0
    missing_context_future = 0
    missing_context_soon = 0
    for row in matches:
        if not isinstance(row, dict):
            continue
        flags = match_window_flags(row, now)
        future += int(flags['future'])
        soon += int(flags['soon'])
        late += int(flags['late'])
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        with_odds += int(bool(coverage.get('odds')))
        with_context += int(bool(coverage.get('context')))
        ready_model += int(bool(coverage.get('ready_for_model')))
        if flags['future'] and not bool(coverage.get('context')):
            missing_context_future += 1
        if flags['soon'] and not bool(coverage.get('context')):
            missing_context_soon += 1

    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    run_seen = deep_int(debug, {'matches_seen'})
    run_offers = deep_int(debug, {'matches_with_offers', 'matches_with_any_offer_source'})
    run_context = deep_int(debug, {'contexts_built', 'matches_with_any_context_source', 'matches_with_merged_context'})
    near_miss_queue = run_near_miss_queue_build()
    next_day_warmup = run_next_day_warmup()

    audit = {
        'status': 'ok' if inventory else 'missing_inventory',
        'updated_at_utc': now.isoformat(),
        'date_local': local_date,
        'inventory_path': str(inventory_path),
        'inventory_exists': bool(inventory),
        'day_provider_stats': provider_stats,
        'day_inventory': {
            'matches_total': max(as_int(counts.get('matches_total')), len(matches)),
            'matches_future': future,
            'matches_soon_6h': soon,
            'matches_already_started': late,
            'matches_with_odds': max(as_int(counts.get('matches_with_odds')), with_odds),
            'matches_with_context': max(as_int(counts.get('matches_with_context')), with_context),
            'matches_ready_for_model': max(as_int(counts.get('matches_ready_for_model')), ready_model),
            'missing_context_future': missing_context_future,
            'missing_context_soon_6h': missing_context_soon,
        },
        'current_run': {
            'matches_seen': run_seen,
            'matches_with_offers': run_offers,
            'matches_with_context': run_context,
            'candidates_raw': as_int(summary.get('candidates_raw')),
            'candidates_before_quality': as_int(summary.get('candidates_before_quality')),
            'publishable': as_int(summary.get('candidates_publishable')),
        },
        'policy': {
            'mode': policy.get('mode'),
            'skip_build': policy.get('skip_build'),
            'reason': policy.get('reason'),
        },
        'coverage_merge': {
            'matches_total': as_int(merge.get('matches_total')),
            'matches_with_odds': as_int(merge.get('matches_with_odds')),
            'matches_with_context': as_int(merge.get('matches_with_context')),
            'coverage_rows_seen': as_int(merge.get('coverage_rows_seen')),
            'runtime_counts': merge.get('runtime_counts') if isinstance(merge.get('runtime_counts'), dict) else {},
        },
        'near_miss_enrichment_queue': near_miss_queue,
        'next_day_warmup': next_day_warmup,
        'missing_context_priority_examples': missing_context_examples(matches, now),
        'explanation': [
            'day_provider_stats shows how many fixtures the day bootstrap sources returned.',
            'day_inventory is cumulative for the whole local date.',
            'current_run is only the current publish/enrichment window and can shrink as matches start.',
            'near_miss_enrichment_queue stores high-EV single-source/proxy candidates for priority confirmation in the next run.',
            'missing_context_priority_examples are the next future matches that enrichment should prefer, especially when they already have odds.',
            'next_day_warmup builds tomorrow inventory after evening local time so overnight matches start accumulating earlier.',
        ],
    }
    write_json(OUT, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
