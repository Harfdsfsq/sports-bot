from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import should_update_current_aliases, write_current_aliases

ROOT = Path('.').resolve()
UTC = timezone.utc
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-merge.json'
SUMMARY_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-summary.json'


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


def max_int_from(row: dict[str, Any], *names: str) -> int:
    best = 0
    containers: list[Any] = [row]
    for key in ('best_candidate', 'source_summary', 'integrity_report', 'metadata'):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        if not isinstance(container, dict):
            continue
        for name in names:
            best = max(best, as_int(container.get(name)))
    return best


def runtime_counts_from_debug(debug: Any) -> dict[str, int]:
    if not isinstance(debug, dict):
        return {}
    return {
        'matches_seen': deep_int(debug, {'matches_seen'}),
        'matches_with_odds': deep_int(debug, {'matches_with_any_offer_source', 'matches_with_offers'}),
        'matches_with_context': deep_int(debug, {'matches_with_any_context_source', 'matches_with_merged_context', 'contexts_built'}),
        'matches_ready_for_model': deep_int(debug, {'matches_with_any_context_source', 'matches_with_merged_context', 'contexts_built'}),
    }


def coverage_index(rows: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if key:
            out[key] = row
    return out


def selected_keys_from_payload(payload: Any) -> set[str]:
    keys: set[str] = set()
    if not isinstance(payload, dict):
        return keys
    for field in ('selected', 'pick', 'published_pick', 'selected_all', 'selected_candidates', 'published_candidates', 'picks'):
        value = payload.get(field)
        if isinstance(value, dict):
            key = str(value.get('match_key') or value.get('canonical_match_id') or '').strip()
            if key:
                keys.add(key)
        elif isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
                    if key:
                        keys.add(key)
    return keys


def provider_stats_from_debug(debug: dict[str, Any]) -> dict[str, Any]:
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    source_stats = summary.get('source_stats') if isinstance(summary.get('source_stats'), dict) else {}
    return source_stats or (debug.get('source_stats') if isinstance(debug.get('source_stats'), dict) else {})


def recompute_counts(matches: list[dict[str, Any]], now: datetime, runtime_counts: dict[str, int]) -> dict[str, int]:
    counts = {
        'matches_total': len(matches),
        'matches_with_odds': 0,
        'matches_with_context': 0,
        'matches_with_weather': 0,
        'matches_with_news': 0,
        'matches_with_xg': 0,
        'matches_with_form': 0,
        'matches_ready_for_model': 0,
        'matches_ready_for_publish': 0,
        'matches_next_6h': 0,
        'matches_next_6h_ready': 0,
        'matches_next_12h': 0,
        'matches_next_12h_ready': 0,
    }
    for row in matches:
        if not isinstance(row, dict):
            continue
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        odds_sources = max_int_from(metadata, 'odds_sources_count', 'independent_odds_sources_count', 'price_sources_count')
        context_sources = max_int_from(metadata, 'context_sources_count', 'confirmation_sources_count')
        has_odds = bool(coverage.get('odds')) or odds_sources > 0
        has_context = bool(coverage.get('context')) or context_sources > 0
        ready = bool(coverage.get('ready_for_model')) or (has_odds and has_context)
        counts['matches_with_odds'] += int(has_odds)
        counts['matches_with_context'] += int(has_context)
        counts['matches_with_weather'] += int(bool(coverage.get('weather')))
        counts['matches_with_news'] += int(bool(coverage.get('news')))
        counts['matches_with_xg'] += int(bool(coverage.get('xg')))
        counts['matches_with_form'] += int(bool(coverage.get('form')))
        counts['matches_ready_for_model'] += int(ready)
        counts['matches_ready_for_publish'] += int(bool(coverage.get('ready_for_publish')))
        kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
        if kickoff is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if 0 <= hours <= 6:
            counts['matches_next_6h'] += 1
            counts['matches_next_6h_ready'] += int(ready)
        if 0 <= hours <= 12:
            counts['matches_next_12h'] += 1
            counts['matches_next_12h_ready'] += int(ready)

    runtime_ready = as_int(runtime_counts.get('matches_ready_for_model'))
    runtime_odds = as_int(runtime_counts.get('matches_with_odds'))
    runtime_context = as_int(runtime_counts.get('matches_with_context'))
    counts['matches_with_odds'] = max(counts['matches_with_odds'], runtime_odds)
    counts['matches_with_context'] = max(counts['matches_with_context'], runtime_context)
    counts['matches_ready_for_model'] = max(counts['matches_ready_for_model'], runtime_ready)
    if counts['matches_next_6h'] and counts['matches_next_6h_ready'] == 0 and runtime_ready > 0:
        counts['matches_next_6h_ready'] = min(counts['matches_next_6h'], runtime_ready)
    if counts['matches_next_12h'] and counts['matches_next_12h_ready'] == 0 and runtime_ready > 0:
        counts['matches_next_12h_ready'] = min(counts['matches_next_12h'], runtime_ready)
    return counts


def main() -> int:
    now_utc = datetime.now(UTC)
    local_date = target_date()
    inventory_path = ROOT / '.data' / 'day_inventory' / f'{local_date}.json'
    inventory = load_json(inventory_path, {})
    if not isinstance(inventory, dict) or not inventory:
        report = {
            'status': 'skipped',
            'reason': 'day_inventory_missing',
            'target_date': local_date,
            'inventory_path': str(inventory_path),
            'updated_at_utc': now_utc.isoformat(),
        }
        write_json(EXPORT_PATH, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    rows = inventory.get('matches') if isinstance(inventory.get('matches'), list) else []
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    runtime_counts = runtime_counts_from_debug(debug)
    coverage_rows = load_json(ROOT / '.data' / 'exports' / 'latest-match-data-coverage-matches.json', [])
    cindex = coverage_index(coverage_rows)
    controlled = load_json(ROOT / 'artifacts' / 'controlled-fallback-report.json', {})
    detailed = load_json(ROOT / '.data' / 'exports' / 'latest-detailed-run-report.json', {})
    selected_keys = selected_keys_from_payload(controlled) | selected_keys_from_payload(detailed)
    source_stats = provider_stats_from_debug(debug if isinstance(debug, dict) else {})

    updated = 0
    for match in rows:
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

        row = cindex.get(key)
        changed = False
        if row:
            candidate_count = as_int(row.get('candidate_count'))
            controlled_count = as_int(row.get('controlled_count'))
            books_max = max_int_from(row, 'books_max', 'books_count', 'odds_books_count')
            odds_sources_max = max_int_from(
                row,
                'odds_sources_max',
                'odds_sources_count',
                'price_sources_count',
                'independent_odds_sources_count',
                'sources_max',
                'sources_count',
            )
            context_sources_max = max_int_from(
                row,
                'context_sources_max',
                'confirmation_sources_max',
                'context_sources_count',
                'confirmation_sources_count',
            )
            # Backward compatibility: old coverage rows used sources_max for odds sources.
            sources_max = max(odds_sources_max, context_sources_max, as_int(row.get('sources_max')))
            has_odds = candidate_count > 0 or books_max > 0 or odds_sources_max > 0
            has_context = candidate_count > 0 or context_sources_max > 0
            ready_for_model = candidate_count > 0 or (has_odds and has_context)
            ready_for_publish = key in selected_keys or controlled_count > 0
            coverage['fixture_core'] = True
            coverage['odds'] = bool(coverage.get('odds')) or has_odds
            coverage['context'] = bool(coverage.get('context')) or has_context
            coverage['ready_for_model'] = bool(coverage.get('ready_for_model')) or ready_for_model
            coverage['ready_for_publish'] = bool(coverage.get('ready_for_publish')) or ready_for_publish
            if has_odds:
                refresh['last_odds_refresh_utc'] = now_utc.isoformat()
            if has_context:
                refresh['last_context_refresh_utc'] = now_utc.isoformat()
            refresh['last_run_coverage_merge_utc'] = now_utc.isoformat()
            metadata['latest_candidate_count'] = candidate_count
            metadata['latest_controlled_count'] = controlled_count
            metadata['latest_books_max'] = books_max
            metadata['latest_sources_max'] = sources_max
            metadata['latest_odds_sources_max'] = odds_sources_max
            metadata['latest_context_sources_max'] = context_sources_max
            metadata['latest_confirmation_sources_max'] = context_sources_max
            metadata['odds_sources_count'] = max(as_int(metadata.get('odds_sources_count')), odds_sources_max)
            metadata['independent_odds_sources_count'] = max(as_int(metadata.get('independent_odds_sources_count')), odds_sources_max)
            metadata['price_sources_count'] = max(as_int(metadata.get('price_sources_count')), odds_sources_max)
            metadata['context_sources_count'] = max(as_int(metadata.get('context_sources_count')), context_sources_max)
            metadata['confirmation_sources_count'] = max(as_int(metadata.get('confirmation_sources_count')), context_sources_max)
            metadata['sources_count'] = max(as_int(metadata.get('sources_count')), sources_max)
            metadata['latest_best_ev_pct'] = round(as_float(row.get('best_ev_pct')), 3)
            metadata['latest_best_edge_pp'] = round(as_float(row.get('best_edge_pp')), 3)
            metadata['latest_best_confidence'] = round(as_float(row.get('best_confidence')), 3)
            changed = True
        if key in selected_keys:
            coverage['ready_for_publish'] = True
            coverage['ready_for_model'] = True
            refresh['last_publishable_refresh_utc'] = now_utc.isoformat()
            changed = True
        if changed:
            updated += 1

    new_counts = recompute_counts(rows, now_utc, runtime_counts)
    previous_counts = inventory.get('counts') if isinstance(inventory.get('counts'), dict) else {}
    merged_counts = dict(previous_counts)
    for key, value in new_counts.items():
        merged_counts[key] = max(as_int(previous_counts.get(key)), as_int(value)) if key.startswith('matches_with_') or key.startswith('matches_ready_') else as_int(value)
    merged_counts['matches_total'] = max(len(rows), as_int(runtime_counts.get('matches_seen')), as_int(previous_counts.get('matches_total')))
    merged_counts['matches_coverage_updated_last_run'] = updated
    merged_counts['coverage_rows_seen_last_run'] = len(cindex)
    merged_counts['runtime_matches_with_odds_last_run'] = as_int(runtime_counts.get('matches_with_odds'))
    merged_counts['runtime_matches_with_context_last_run'] = as_int(runtime_counts.get('matches_with_context'))
    inventory['counts'] = merged_counts
    inventory['updated_at_utc'] = now_utc.isoformat()
    sources = inventory.setdefault('sources', {})
    if not isinstance(sources, dict):
        sources = {}
        inventory['sources'] = sources
    sources['latest_coverage_merge'] = {
        'updated_at_utc': now_utc.isoformat(),
        'coverage_rows_seen': len(cindex),
        'matches_updated': updated,
        'runtime_counts': runtime_counts,
        'source_stats_seen': sorted(source_stats.keys()) if isinstance(source_stats, dict) else [],
    }

    base = ROOT / '.data' / 'day_inventory'
    write_json(inventory_path, inventory)
    alias_update = write_current_aliases(ROOT, local_date, inventory, write_json)

    summary = {
        'date_local': local_date,
        'updated_at_utc': now_utc.isoformat(),
        'timezone': inventory.get('timezone') or str(app_tz()),
        'build_status': inventory.get('build_status') or 'ok',
        'counts': merged_counts,
        'source_match_counts': dict(inventory.get('source_match_counts') or {}),
        'league_match_counts': dict(inventory.get('league_match_counts') or {}),
        'sources': dict(inventory.get('sources') or {}),
        'alias_update': alias_update,
    }
    if should_update_current_aliases(local_date):
        write_json(SUMMARY_PATH, summary)

    report = {
        'status': 'ok',
        'target_date': local_date,
        'updated_at_utc': now_utc.isoformat(),
        'inventory_path': str(inventory_path),
        'summary_path': str(SUMMARY_PATH) if should_update_current_aliases(local_date) else None,
        'alias_update': alias_update,
        'summary_path': str(SUMMARY_PATH),
        'matches_total': merged_counts.get('matches_total'),
        'coverage_rows_seen': len(cindex),
        'runtime_counts': runtime_counts,
        'matches_updated': updated,
        'matches_with_odds': merged_counts.get('matches_with_odds'),
        'matches_with_context': merged_counts.get('matches_with_context'),
        'matches_ready_for_model': merged_counts.get('matches_ready_for_model'),
        'matches_next_6h': merged_counts.get('matches_next_6h'),
        'matches_next_6h_ready': merged_counts.get('matches_next_6h_ready'),
        'matches_next_12h': merged_counts.get('matches_next_12h'),
        'matches_next_12h_ready': merged_counts.get('matches_next_12h_ready'),
        'matches_ready_for_publish': merged_counts.get('matches_ready_for_publish'),
        'selected_keys_seen': len(selected_keys),
        'saved_paths': [str(path) for path in paths],
        'notes': [
            'Near-window inventory is recomputed from now, not from the inventory calendar boundary.',
            'The Telegram detailed report now reads the post-merge summary instead of stale pre-run bootstrap counts.',
            'If per-match coverage rows are sparse, runtime debug counters are used as a conservative readiness floor for 6h/12h windows.',
            'Separate odds/context source counters are persisted in match metadata for cumulative coverage and publish-readiness audits.',
        ],
    }
    write_json(EXPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
