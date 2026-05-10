from __future__ import annotations

"""Project real run source counts back into day inventory.

The bot uses odds_api_io as the primary odds API, but a single API response can
contain several independent bookmaker prices. For coverage planning we keep both
signals:

* independent_odds_sources_count: independent API/provider count
* price_confirmation_sources_count: usable pricing confirmations, normally the
  max of independent odds providers and distinct bookmakers/lines

Publication still remains guarded elsewhere; this file only repairs inventory
metadata so priority and cumulative coverage can target matches that still need
2+ pricing confirmations and 2+ context confirmations.
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
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
OUT = EXPORT_DIR / 'latest-inventory-source-count-repair.json'

CANDIDATE_PATHS = [
    EXPORT_DIR / 'latest-rescue-candidates.json',
    EXPORT_DIR / 'latest-candidates-before-quality.json',
    EXPORT_DIR / 'latest-candidates-after-quality.json',
    EXPORT_DIR / 'latest-candidates.json',
    EXPORT_DIR / 'latest-controlled-fallback-report.json',
    ROOT / 'artifacts' / 'controlled-fallback-report.json',
]


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


def target_date(now: datetime) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or now.astimezone(app_tz()).date().isoformat()


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


def norm_source(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'the_odds_api': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro',
        'sstats_form': 'sstats',
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/\s]+', value) if v.strip()]
    return []


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return out
    for key in ('candidates', 'rows', 'data', 'selected', 'selected_all', 'published_candidates', 'top_candidates', 'evaluated', 'blocked_top', 'near_miss'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(dict(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.append(dict(value))
    decision = payload.get('decision')
    if isinstance(decision, dict):
        out.extend(candidate_rows(decision))
    return out


def raw_bucket(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    value = candidate.get('raw_bucket_offers')
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    diagnostics = candidate.get('diagnostics') if isinstance(candidate.get('diagnostics'), dict) else {}
    value = diagnostics.get('raw_bucket_offers') if isinstance(diagnostics, dict) else None
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    return []


def deep_count(row: dict[str, Any], *names: str) -> int:
    best = 0
    stack: list[Any] = [row]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for k, v in item.items():
                if k in names:
                    best = max(best, as_int(v))
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(item, list):
            stack.extend(v for v in item if isinstance(v, (dict, list)))
    return best


def candidate_source_counts(candidate: dict[str, Any]) -> dict[str, int]:
    offers = raw_bucket(candidate)
    books = {str(o.get('bookmaker') or '').strip().lower() for o in offers if str(o.get('bookmaker') or '').strip()}
    odds_sources = {norm_source(o.get('source')) for o in offers if norm_source(o.get('source'))}
    summary = candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {}
    for key in ('odds_sources', 'price_sources', 'exact_price_sources'):
        for src in list_from_any(candidate.get(key)) + list_from_any(summary.get(key)):
            srcn = norm_source(src)
            if srcn:
                odds_sources.add(srcn)
    selected_source = norm_source(summary.get('selected_source') or summary.get('source') or candidate.get('source'))
    if selected_source:
        odds_sources.add(selected_source)
    context_sources = set()
    for key in ('confirmation_sources', 'context_sources', 'providers', 'merged_context_sources'):
        for src in list_from_any(candidate.get(key)) + list_from_any(summary.get(key)):
            srcn = norm_source(src)
            if srcn and srcn not in {'market', 'odds_api_io'}:
                context_sources.add(srcn)
    context_source = norm_source(summary.get('context_source') or candidate.get('context_source'))
    if context_source and context_source not in {'market', 'odds_api_io', 'ensemble'}:
        context_sources.add(context_source)
    independent_odds = max(len(odds_sources), deep_count(candidate, 'odds_sources_count', 'price_sources_count', 'exact_sources_count'))
    book_count = max(len(books), deep_count(candidate, 'books_count', 'odds_books_count', 'paired_books', 'exact_line_bookmakers_count'))
    context_count = max(len(context_sources), deep_count(candidate, 'context_sources_count', 'confirmation_sources_count'))
    return {
        'independent_odds_sources_count': independent_odds,
        'books_count': book_count,
        'price_confirmation_sources_count': max(independent_odds, book_count),
        'context_sources_count': context_count,
        'confirmation_sources_count': context_count,
    }


def merge_max(dst: dict[str, Any], values: dict[str, int]) -> bool:
    changed = False
    for key, value in values.items():
        old = as_int(dst.get(key))
        if value > old:
            dst[key] = value
            changed = True
    # Keep old field names compatible with reports.
    if as_int(dst.get('price_confirmation_sources_count')) > as_int(dst.get('price_sources_count')):
        dst['price_sources_count'] = dst['price_confirmation_sources_count']
        changed = True
    if as_int(dst.get('independent_odds_sources_count')) > as_int(dst.get('odds_sources_count')):
        dst['odds_sources_count'] = dst['independent_odds_sources_count']
        changed = True
    return changed


def rows_by_match_from_candidates() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        if payload in (None, {}, []):
            continue
        for candidate in candidate_rows(payload):
            key = str(candidate.get('match_key') or candidate.get('canonical_match_id') or '').strip()
            if not key:
                continue
            current = out.setdefault(key, {})
            merge_max(current, candidate_source_counts(candidate))
    return out


def rows_by_match_from_coverage() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    rows = load_json(EXPORT_DIR / 'latest-match-data-coverage-matches.json', [])
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not key:
            continue
        books = max(
            as_int(row.get('books_max')),
            as_int(row.get('books_count')),
            as_int(row.get('odds_books_count')),
        )
        odds = max(
            as_int(row.get('odds_sources_max')),
            as_int(row.get('odds_sources_count')),
            as_int(row.get('sources_max')),
            as_int(row.get('sources_count')),
        )
        context = max(
            as_int(row.get('context_sources_max')),
            as_int(row.get('confirmation_sources_max')),
            as_int(row.get('context_sources_count')),
            as_int(row.get('confirmation_sources_count')),
        )
        out[key] = {
            'independent_odds_sources_count': odds,
            'books_count': books,
            'price_confirmation_sources_count': max(odds, books),
            'context_sources_count': context,
            'confirmation_sources_count': context,
        }
    return out


def recompute_inventory_counts(matches: list[dict[str, Any]], previous: dict[str, Any], min_price: int, min_context: int) -> dict[str, Any]:
    counts = dict(previous or {})
    price_2plus = context_2plus = publish_ready = 0
    for row in matches:
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        price_sources = max(
            as_int(metadata.get('price_confirmation_sources_count')),
            as_int(metadata.get('latest_books_max')),
            as_int(metadata.get('books_count')),
            as_int(metadata.get('price_sources_count')),
        )
        context_sources = max(
            as_int(metadata.get('context_sources_count')),
            as_int(metadata.get('confirmation_sources_count')),
            as_int(metadata.get('latest_context_sources_max')),
            as_int(metadata.get('latest_confirmation_sources_max')),
        )
        ok_price = price_sources >= min_price
        ok_context = context_sources >= min_context
        price_2plus += int(ok_price)
        context_2plus += int(ok_context)
        ready = ok_price and ok_context and bool(coverage.get('odds')) and bool(coverage.get('context'))
        coverage['odds_2plus_sources'] = ok_price
        coverage['context_2plus_sources'] = ok_context
        coverage['ready_for_publish'] = bool(coverage.get('ready_for_publish')) or ready
        row['coverage'] = coverage
        publish_ready += int(bool(coverage.get('ready_for_publish')))
    counts['matches_with_2plus_price_confirmations'] = price_2plus
    counts['matches_with_2plus_context_sources'] = context_2plus
    counts['matches_ready_for_publish'] = max(as_int(counts.get('matches_ready_for_publish')), publish_ready)
    counts['publish_min_price_confirmations'] = min_price
    counts['publish_min_context_sources'] = min_context
    return counts


def main() -> int:
    now = datetime.now(UTC)
    d = target_date(now)
    inv_path = DAY_INV_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        inv = {'date_local': d, 'matches': []}
    matches = [row for row in inv.get('matches', []) if isinstance(row, dict)]
    evidence = rows_by_match_from_coverage()
    for key, counts in rows_by_match_from_candidates().items():
        current = evidence.setdefault(key, {})
        merge_max(current, counts)
    repaired = 0
    for row in matches:
        key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not key or key not in evidence:
            continue
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        before = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        if merge_max(metadata, evidence[key]):
            metadata['source_count_repair_updated_utc'] = now.isoformat()
        row['metadata'] = metadata
        after = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        repaired += int(before != after)
    min_price = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    inv['matches'] = matches
    inv['counts'] = recompute_inventory_counts(matches, inv.get('counts') if isinstance(inv.get('counts'), dict) else {}, min_price, min_context)
    inv['updated_at_utc'] = now.isoformat()
    sources = inv.setdefault('sources', {})
    if isinstance(sources, dict):
        sources['source_count_repair'] = {
            'updated_at_utc': now.isoformat(),
            'evidence_matches': len(evidence),
            'rows_repaired': repaired,
            'min_price_confirmations': min_price,
            'min_context_sources': min_context,
        }
    for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
        write_json(path, inv)
    report = {
        'status': 'ok',
        'date_local': d,
        'updated_at_utc': now.isoformat(),
        'inventory_path': str(inv_path),
        'evidence_matches': len(evidence),
        'rows_repaired': repaired,
        'counts': inv.get('counts', {}),
        'notes': [
            'price_confirmation_sources_count uses distinct bookmaker prices as confirmations when only one odds API provider is available.',
            'independent_odds_sources_count remains separate and is not inflated by bookmaker count.',
            'ready_for_publish audit now requires 2+ pricing confirmations and 2+ context confirmations.',
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
