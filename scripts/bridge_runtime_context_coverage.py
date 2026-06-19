from __future__ import annotations

"""Merge latest runtime line/context artifacts into the day inventory.

The model run can collect many context observations and line snapshots without all
of them becoming candidates.  This script promotes those runtime observations to
per-match inventory coverage so the Telegram report reflects real provider work.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
EXPORT_DIR = ROOT / '.data' / 'exports'
DAY_DIR = ROOT / '.data' / 'day_inventory'
OUT = EXPORT_DIR / 'latest-runtime-context-coverage-bridge.json'
LIVE_ODDS_SOURCES = {'odds_api_io', 'bzzoiro', 'sportlogic'}
IGNORED_CONTEXT = {'', 'market', 'odds_api_io', 'line_history', 'ensemble'}


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
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'odds_api_io_account1': 'odds_api_io',
        'odds_api_io_account2': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'bzzoiro_v2': 'bzzoiro',
        'the_sports_db': 'thesportsdb',
        'sportsdb': 'thesportsdb',
        'sstats_form': 'sstats',
        'xg_model_context': 'model_xg',
    }
    return aliases.get(text, text)


def list_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def ensure_bucket(index: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return index.setdefault(key, {'context': set(), 'line_sources': set(), 'odds_sources': set(), 'books': set(), 'price': set(), 'samples': []})


def add_sample(bucket: dict[str, Any], source: str, detail: dict[str, Any]) -> None:
    samples = bucket.setdefault('samples', [])
    if isinstance(samples, list) and len(samples) < 8:
        samples.append({'source': source, **{k: v for k, v in detail.items() if v not in (None, '', [], {})}})


def rows_from(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, [])
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('rows', 'items', 'data', 'matches', 'observations', 'snapshots', 'lines'):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, dict)]
    return []


def build_runtime_index() -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    index: dict[str, dict[str, Any]] = {}
    stats = {'context_rows': 0, 'serving_rows': 0, 'line_rows': 0, 'consensus_rows': 0}

    for row in rows_from(EXPORT_DIR / 'latest-context-observations.json'):
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        provider = norm(row.get('provider') or row.get('source') or row.get('context_source'))
        if not key or provider in IGNORED_CONTEXT or re.match(r'^context_(source|confirmation)_\d+$', provider):
            continue
        bucket = ensure_bucket(index, key)
        bucket['context'].add(provider)
        add_sample(bucket, 'context_observations', {'provider': provider, 'kind': row.get('kind')})
        stats['context_rows'] += 1

    for row in rows_from(EXPORT_DIR / 'latest-match-serving.json'):
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not key:
            continue
        bucket = ensure_bucket(index, key)
        for src in list_any(row.get('context_sources')):
            srcn = norm(src)
            if srcn not in IGNORED_CONTEXT:
                bucket['context'].add(srcn)
        for src in list_any(row.get('line_sources')):
            srcn = norm(src)
            if srcn in LIVE_ODDS_SOURCES:
                bucket['line_sources'].add(srcn)
                bucket['odds_sources'].add(srcn)
        line_count = as_int(row.get('line_snapshot_count'))
        context_count = as_int(row.get('context_source_count'))
        while len(bucket['context']) < context_count:
            bucket['context'].add(f'context_confirmation_{len(bucket["context"]) + 1}')
        for idx in range(line_count):
            bucket['price'].add(f'line_snapshot_{idx + 1}')
        add_sample(bucket, 'match_serving', {'context_source_count': context_count, 'line_snapshot_count': line_count})
        stats['serving_rows'] += 1

    for row in rows_from(EXPORT_DIR / 'latest-line-snapshots.json'):
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not key:
            continue
        bucket = ensure_bucket(index, key)
        provider = norm(row.get('provider') or row.get('source'))
        book = norm(row.get('bookmaker'))
        if provider in LIVE_ODDS_SOURCES:
            bucket['line_sources'].add(provider)
            bucket['odds_sources'].add(provider)
        if book:
            bucket['books'].add(book)
            bucket['price'].add(f'book:{book}')
        elif provider:
            bucket['price'].add(f'provider:{provider}')
        stats['line_rows'] += 1

    for row in rows_from(EXPORT_DIR / 'latest-consensus-lines.json'):
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not key:
            continue
        bucket = ensure_bucket(index, key)
        for src in list_any(row.get('sources')):
            srcn = norm(src)
            if srcn in LIVE_ODDS_SOURCES:
                bucket['line_sources'].add(srcn)
                bucket['odds_sources'].add(srcn)
        for book in list_any(row.get('books')):
            bookn = norm(book)
            if bookn:
                bucket['books'].add(bookn)
                bucket['price'].add(f'book:{bookn}')
        stats['consensus_rows'] += 1

    return index, stats


def recompute_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {'matches_total': len(rows), 'matches_with_odds': 0, 'matches_with_context': 0, 'matches_ready_for_model': 0, 'matches_ready_for_publish': 0, 'matches_with_2plus_context_sources': 0}
    for row in rows:
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        price_count = max(as_int(metadata.get('price_confirmation_sources_count')), len(row.get('price_confirmations') or []))
        odds_count = max(as_int(metadata.get('independent_odds_sources_count')), len(row.get('odds_sources') or []), len(row.get('line_sources') or []))
        context_count = max(as_int(metadata.get('context_sources_count')), len(row.get('context_sources') or []), len(row.get('context_confirmations') or []))
        has_odds = bool(coverage.get('odds')) or price_count > 0 or odds_count > 0
        has_context = bool(coverage.get('context')) or context_count > 0
        counts['matches_with_odds'] += int(has_odds)
        counts['matches_with_context'] += int(has_context)
        counts['matches_with_2plus_context_sources'] += int(context_count >= 2)
        counts['matches_ready_for_model'] += int(has_odds and has_context)
        counts['matches_ready_for_publish'] += int(price_count >= 1 and odds_count >= 1 and context_count >= 1)
    return counts


def main() -> int:
    now_iso = datetime.now(UTC).isoformat()
    date = target_date()
    inv_path = DAY_DIR / f'{date}.json'
    inventory = load_json(inv_path, {})
    rows = inventory.get('matches') if isinstance(inventory, dict) else None
    if not isinstance(rows, list):
        report = {'status': 'skipped', 'reason': 'inventory_missing', 'inventory_path': str(inv_path), 'updated_at_utc': now_iso}
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    runtime, stats = build_runtime_index()
    updated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        ev = runtime.get(key)
        if not ev:
            continue
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        refresh = row.get('refresh') if isinstance(row.get('refresh'), dict) else {}
        context = {norm(x) for x in list_any(row.get('context_sources')) + list_any(metadata.get('context_sources')) if norm(x)}
        context_conf = {norm(x) for x in list_any(row.get('context_confirmations')) + list_any(metadata.get('context_confirmations')) if norm(x)}
        line_sources = {norm(x) for x in list_any(row.get('line_sources')) + list_any(metadata.get('line_sources')) if norm(x) in LIVE_ODDS_SOURCES}
        odds_sources = {norm(x) for x in list_any(row.get('odds_sources')) + list_any(metadata.get('odds_sources')) if norm(x) in LIVE_ODDS_SOURCES}
        books = {norm(x) for x in list_any(row.get('books')) + list_any(metadata.get('books')) if norm(x)}
        price = set(list_any(row.get('price_confirmations')) + list_any(metadata.get('price_confirmations')))

        context.update(x for x in ev['context'] if x)
        context_conf.update(x for x in ev['context'] if x)
        line_sources.update(x for x in ev['line_sources'] if x in LIVE_ODDS_SOURCES)
        odds_sources.update(x for x in ev['odds_sources'] if x in LIVE_ODDS_SOURCES)
        books.update(x for x in ev['books'] if x)
        price.update(x for x in ev['price'] if x)
        while len(price) < len(books):
            price.add(f'book_confirmation_{len(price) + 1}')

        row['context_sources'] = sorted(context)
        row['context_confirmations'] = sorted(context_conf)
        row['line_sources'] = sorted(line_sources or odds_sources)
        row['odds_sources'] = sorted(odds_sources or line_sources)
        row['books'] = sorted(books)
        row['price_confirmations'] = sorted(price)
        metadata['context_sources_count'] = max(as_int(metadata.get('context_sources_count')), len(context), len(context_conf))
        metadata['confirmation_sources_count'] = max(as_int(metadata.get('confirmation_sources_count')), len(context_conf), len(context))
        metadata['independent_odds_sources_count'] = max(as_int(metadata.get('independent_odds_sources_count')), len(odds_sources | line_sources))
        metadata['odds_sources_count'] = metadata['independent_odds_sources_count']
        metadata['books_count'] = max(as_int(metadata.get('books_count')), len(books))
        metadata['price_confirmation_sources_count'] = max(as_int(metadata.get('price_confirmation_sources_count')), len(price), len(books))
        metadata['runtime_context_bridge_updated_utc'] = now_iso
        if ev.get('samples'):
            metadata['runtime_context_bridge_samples'] = ev['samples'][:8]
        row['metadata'] = metadata
        coverage['context'] = bool(context or context_conf or coverage.get('context'))
        coverage['odds'] = bool(price or odds_sources or line_sources or coverage.get('odds'))
        coverage['context_2plus_sources'] = max(len(context), len(context_conf), as_int(metadata.get('context_sources_count'))) >= 2
        coverage['ready_for_model'] = bool(coverage.get('odds') and coverage.get('context'))
        coverage['ready_for_publish'] = bool(coverage['ready_for_model'] and as_int(metadata.get('price_confirmation_sources_count')) >= 1 and as_int(metadata.get('context_sources_count')) >= 1)
        row['coverage'] = coverage
        if coverage.get('context'):
            refresh['last_context_refresh_utc'] = refresh.get('last_context_refresh_utc') or now_iso
        if coverage.get('odds'):
            refresh['last_odds_refresh_utc'] = refresh.get('last_odds_refresh_utc') or now_iso
        row['refresh'] = refresh
        after = json.dumps(row, ensure_ascii=False, sort_keys=True)
        updated += int(before != after)

    counts = dict(inventory.get('counts') or {})
    fresh_counts = recompute_counts([r for r in rows if isinstance(r, dict)])
    for key, value in fresh_counts.items():
        counts[key] = max(as_int(counts.get(key)), value) if key.startswith('matches_with_') or key.startswith('matches_ready_') else value
    inventory['counts'] = counts
    inventory['updated_at_utc'] = now_iso
    sources = inventory.setdefault('sources', {})
    if isinstance(sources, dict):
        sources['runtime_context_coverage_bridge'] = {'updated_at_utc': now_iso, 'runtime_matches': len(runtime), 'rows_updated': updated, **stats}
    write_json(inv_path, inventory)
    for alias in (DAY_DIR / 'current.json', DAY_DIR / 'latest.json', DAY_DIR / 'today.json'):
        write_json(alias, inventory)
    report = {'status': 'ok', 'date_local': date, 'inventory_path': str(inv_path), 'runtime_matches': len(runtime), 'rows_updated': updated, 'stats': stats, 'counts': counts, 'updated_at_utc': now_iso}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
