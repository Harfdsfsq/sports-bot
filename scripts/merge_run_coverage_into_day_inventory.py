from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-merge.json'


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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def parse_target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def bool_or_old(old: Any, new: bool) -> bool:
    return bool(old) or bool(new)


def build_coverage_index(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('match_key') or '').strip()
        if not key:
            continue
        out[key] = row
    return out


def provider_stats_from_debug(debug: dict[str, Any]) -> dict[str, Any]:
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    source_stats = summary.get('source_stats') if isinstance(summary.get('source_stats'), dict) else {}
    if source_stats:
        return source_stats
    return debug.get('source_stats') if isinstance(debug.get('source_stats'), dict) else {}


def selected_keys_from_payload(payload: Any) -> set[str]:
    keys: set[str] = set()
    if not isinstance(payload, dict):
        return keys
    for field in ('selected', 'selected_candidates', 'published_candidates', 'picks'):
        rows = payload.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                key = str(row.get('match_key') or '').strip()
                if key:
                    keys.add(key)
    return keys


def main() -> int:
    now_utc = datetime.now(UTC)
    target_date = parse_target_date()
    inventory_path = ROOT / '.data' / 'day_inventory' / f'{target_date}.json'
    inventory = load_json(inventory_path, {})
    if not isinstance(inventory, dict) or not inventory:
        report = {
            'status': 'skipped',
            'reason': 'day_inventory_missing',
            'target_date': target_date,
            'inventory_path': str(inventory_path),
            'updated_at_utc': now_utc.isoformat(),
        }
        write_json(EXPORT_PATH, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    coverage_rows = load_json(ROOT / '.data' / 'exports' / 'latest-match-data-coverage-matches.json', [])
    coverage_index = build_coverage_index(coverage_rows)
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    controlled = load_json(ROOT / 'artifacts' / 'controlled-fallback-report.json', {})
    detailed = load_json(ROOT / '.data' / 'exports' / 'latest-detailed-run-report.json', {})
    source_stats = provider_stats_from_debug(debug if isinstance(debug, dict) else {})
    selected_keys = selected_keys_from_payload(controlled) | selected_keys_from_payload(detailed)

    matches = inventory.get('matches') if isinstance(inventory.get('matches'), list) else []
    updated = 0
    odds_marked = 0
    context_marked = 0
    model_marked = 0
    publish_marked = 0

    for match in matches:
        if not isinstance(match, dict):
            continue
        key = str(match.get('match_key') or match.get('canonical_match_id') or '').strip()
        if not key:
            continue
        coverage = match.setdefault('coverage', {})
        if not isinstance(coverage, dict):
            coverage = {}
            match['coverage'] = coverage
        refresh = match.setdefault('refresh', {})
        if not isinstance(refresh, dict):
            refresh = {}
            match['refresh'] = refresh
        metadata = match.setdefault('metadata', {})
        if not isinstance(metadata, dict):
            metadata = {}
            match['metadata'] = metadata

        row = coverage_index.get(key)
        was_changed = False
        if row:
            candidate_count = as_int(row.get('candidate_count'))
            controlled_count = as_int(row.get('controlled_count'))
            books_max = as_int(row.get('books_max'))
            sources_max = as_int(row.get('sources_max'))
            best = row.get('best_candidate') if isinstance(row.get('best_candidate'), dict) else {}
            best_ev = as_float(row.get('best_ev_pct'))
            best_edge = as_float(row.get('best_edge_pp'))
            best_conf = as_float(row.get('best_confidence'))

            has_odds = candidate_count > 0 or books_max > 0
            has_context = candidate_count > 0 or sources_max > 1
            ready_for_model = candidate_count > 0
            ready_for_publish = key in selected_keys or controlled_count > 0

            coverage['fixture_core'] = bool_or_old(coverage.get('fixture_core'), True)
            coverage['odds'] = bool_or_old(coverage.get('odds'), has_odds)
            coverage['context'] = bool_or_old(coverage.get('context'), has_context)
            coverage['ready_for_model'] = bool_or_old(coverage.get('ready_for_model'), ready_for_model)
            coverage['ready_for_publish'] = bool_or_old(coverage.get('ready_for_publish'), ready_for_publish)

            if has_odds:
                refresh['last_odds_refresh_utc'] = now_utc.isoformat()
            if has_context:
                refresh['last_context_refresh_utc'] = now_utc.isoformat()
            refresh['last_run_coverage_merge_utc'] = now_utc.isoformat()

            metadata['latest_candidate_count'] = candidate_count
            metadata['latest_controlled_count'] = controlled_count
            metadata['latest_books_max'] = books_max
            metadata['latest_sources_max'] = sources_max
            metadata['latest_best_ev_pct'] = round(best_ev, 3)
            metadata['latest_best_edge_pp'] = round(best_edge, 3)
            metadata['latest_best_confidence'] = round(best_conf, 3)
            if best:
                metadata['latest_best_candidate'] = {
                    'family': best.get('family'),
                    'selection': best.get('selection'),
                    'point': best.get('point'),
                    'odds': best.get('odds'),
                    'origin': best.get('origin'),
                }
            families = row.get('families') if isinstance(row.get('families'), dict) else {}
            reject_reasons = row.get('reject_reasons') if isinstance(row.get('reject_reasons'), dict) else {}
            missing_flags = row.get('missing_flags') if isinstance(row.get('missing_flags'), dict) else {}
            metadata['latest_market_families'] = families
            metadata['latest_reject_reasons_top'] = dict(list(reject_reasons.items())[:10])
            metadata['latest_missing_flags'] = missing_flags
            was_changed = True

        if key in selected_keys:
            coverage['ready_for_publish'] = True
            refresh['last_publishable_refresh_utc'] = now_utc.isoformat()
            was_changed = True

        if was_changed:
            updated += 1

        if bool(coverage.get('odds')):
            odds_marked += 1
        if bool(coverage.get('context')):
            context_marked += 1
        if bool(coverage.get('ready_for_model')):
            model_marked += 1
        if bool(coverage.get('ready_for_publish')):
            publish_marked += 1

    inventory['updated_at_utc'] = now_utc.isoformat()
    sources = inventory.setdefault('sources', {})
    if not isinstance(sources, dict):
        sources = {}
        inventory['sources'] = sources
    sources['latest_coverage_merge'] = {
        'updated_at_utc': now_utc.isoformat(),
        'coverage_rows_seen': len(coverage_index),
        'matches_updated': updated,
        'source_stats_seen': sorted(source_stats.keys()) if isinstance(source_stats, dict) else [],
    }

    counts = inventory.setdefault('counts', {})
    if not isinstance(counts, dict):
        counts = {}
        inventory['counts'] = counts
    counts['matches_total'] = len(matches)
    counts['matches_with_odds'] = odds_marked
    counts['matches_with_context'] = context_marked
    counts['matches_ready_for_model'] = model_marked
    counts['matches_ready_for_publish'] = publish_marked
    counts['matches_coverage_updated_last_run'] = updated

    base = ROOT / '.data' / 'day_inventory'
    paths = [
        inventory_path,
        base / 'latest.json',
        base / 'current.json',
        base / 'today.json',
    ]
    for path in paths:
        write_json(path, inventory)

    summary = {
        'status': 'ok',
        'target_date': target_date,
        'updated_at_utc': now_utc.isoformat(),
        'inventory_path': str(inventory_path),
        'matches_total': len(matches),
        'coverage_rows_seen': len(coverage_index),
        'matches_updated': updated,
        'matches_with_odds': odds_marked,
        'matches_with_context': context_marked,
        'matches_ready_for_model': model_marked,
        'matches_ready_for_publish': publish_marked,
        'selected_keys_seen': len(selected_keys),
        'saved_paths': [str(path) for path in paths],
        'notes': [
            'Coverage is cumulative: true flags are preserved across two-hour runs.',
            'This step does not publish or relax quality filters; it only records what data the run already produced.',
        ],
    }
    write_json(EXPORT_PATH, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
