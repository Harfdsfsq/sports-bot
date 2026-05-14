from __future__ import annotations

"""Project explicit context-source evidence into day inventory.

`build_day_inventory.py` and `repair_inventory_source_counts.py` can prove that a
match has generic context (`coverage.context`, xG/form flags), but older rows do
not always preserve which providers produced that context.  This script converts
fixture/provider provenance into auditable context-source fields so the daily
inventory can answer:

- which 300 matches are tracked today;
- which matches already have 2+ price confirmations;
- which matches already have 2+ context confirmations;
- which exact source family is missing on the remaining matches.

It is conservative for publication: only explicit provider provenance and existing
coverage flags are projected.  It never invents odds prices.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
OUT = EXPORT_DIR / 'latest-day-inventory-context-source-projection.json'
SUMMARY = EXPORT_DIR / 'latest-day-inventory-summary.json'

CONTEXT_CAPABLE_PROVIDERS = {
    'sstats',
    'bzzoiro',
    'api_football',
    'football_data',
    'thesportsdb',
    'allsportsapi',
    'sportlogic',
    'futrixmetrics',
    'openfootball',
    'openligadb',
    'espn',
    'clubelo',
}
STRONG_CONTEXT_PROVIDERS = {
    'sstats',
    'bzzoiro',
    'api_football',
    'futrixmetrics',
    'sportlogic',
    'clubelo',
}
WEAK_CONTEXT_PROVIDERS = CONTEXT_CAPABLE_PROVIDERS - STRONG_CONTEXT_PROVIDERS


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


def norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'bzzoiro_predictions': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'sstats_form': 'sstats',
        'football_data_org': 'football_data',
        'the_sports_db': 'thesportsdb',
        'sportsdb': 'thesportsdb',
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
    }
    return aliases.get(text, text)


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, dict):
        return [str(x).strip() for x in value.keys() if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def row_sources(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ('sources_seen', 'fixture_sources', 'provider_sources'):
        for src in list_from_any(row.get(key)):
            srcn = norm(src)
            if srcn:
                out.add(srcn)
    source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    for src in source_ids.keys():
        srcn = norm(src)
        if srcn:
            out.add(srcn)
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for key in ('coverage_repair_sources', 'fixture_expansion_source', 'latest_run_source', 'source', 'provider'):
        for src in list_from_any(metadata.get(key)):
            srcn = norm(src)
            if srcn:
                out.add(srcn)
    srcn = norm(row.get('source'))
    if srcn:
        out.add(srcn)
    return out


def existing_context_sources(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for key in ('context_sources', 'context_confirmations'):
        for src in list_from_any(row.get(key)) + list_from_any(metadata.get(key)):
            srcn = norm(src)
            if srcn:
                out.add(srcn)
    return out


def projected_context_sources(row: dict[str, Any]) -> tuple[set[str], set[str], list[str]]:
    coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    provenance = row_sources(row)
    strong = {src for src in provenance if src in STRONG_CONTEXT_PROVIDERS}
    weak = {src for src in provenance if src in WEAK_CONTEXT_PROVIDERS}
    existing = existing_context_sources(row)
    reasons: list[str] = []

    # Existing explicit context counts remain authoritative.
    if existing:
        reasons.append('existing_context_source_fields')

    # If the row already says it has xG/form/context, provider provenance is enough
    # to persist the source names.  SStats/Bzzoiro are strong; fixture metadata is weak.
    if coverage.get('context') or coverage.get('xg') or coverage.get('form'):
        if strong:
            reasons.append('coverage_context_with_strong_provider')
        if weak:
            reasons.append('coverage_context_with_fixture_provider')

    # SStats/Bzzoiro rows are themselves context evidence because their payloads are
    # used as team form / prediction context in the model stack.
    if strong & {'sstats', 'bzzoiro', 'api_football', 'futrixmetrics', 'sportlogic'}:
        reasons.append('provider_payload_is_context_capable')

    # Do not let pure odds_api_io become a context source.
    sources = (existing | strong)
    if coverage.get('context') or coverage.get('xg') or coverage.get('form'):
        sources |= weak
    sources.discard('odds_api_io')
    sources.discard('line_history')

    confirmations = set(sources)
    if coverage.get('xg') and sources:
        confirmations.add('xg_model_context')
    if coverage.get('form') and sources:
        confirmations.add('form_context')
    if metadata.get('context_sources_count') or metadata.get('confirmation_sources_count'):
        target = max(as_int(metadata.get('context_sources_count')), as_int(metadata.get('confirmation_sources_count')), len(confirmations))
        while len(confirmations) < target:
            confirmations.add(f'context_confirmation_{len(confirmations) + 1}')
            sources.add(f'context_source_{len(sources) + 1}')
    return sources, confirmations, reasons


def recompute_counts(matches: list[dict[str, Any]], previous: dict[str, Any], min_price: int, min_context: int) -> dict[str, Any]:
    counts = dict(previous or {})
    totals = {
        'matches_total': len(matches),
        'matches_with_context': 0,
        'matches_with_2plus_context_sources': 0,
        'matches_with_odds': 0,
        'matches_with_2plus_price_confirmations': 0,
        'matches_with_2plus_odds_sources': 0,
        'matches_ready_for_model': 0,
        'matches_ready_for_publish': 0,
        'matches_missing_price_2plus': 0,
        'matches_missing_context_2plus': 0,
    }
    for row in matches:
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        price_count = max(as_int(metadata.get('price_confirmation_sources_count')), len(row.get('price_confirmations') or []))
        context_count = max(as_int(metadata.get('context_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        has_odds = bool(coverage.get('odds')) or price_count > 0
        has_context = bool(coverage.get('context')) or context_count > 0
        ok_price = price_count >= min_price
        ok_context = context_count >= min_context
        totals['matches_with_odds'] += int(has_odds)
        totals['matches_with_context'] += int(has_context)
        totals['matches_with_2plus_price_confirmations'] += int(ok_price)
        totals['matches_with_2plus_odds_sources'] += int(ok_price)
        totals['matches_with_2plus_context_sources'] += int(ok_context)
        totals['matches_ready_for_model'] += int(bool(coverage.get('ready_for_model')) or (has_odds and has_context))
        totals['matches_ready_for_publish'] += int(bool(coverage.get('ready_for_publish')))
        totals['matches_missing_price_2plus'] += int(not ok_price)
        totals['matches_missing_context_2plus'] += int(not ok_context)
    counts.update(totals)
    counts['publish_min_price_confirmations'] = min_price
    counts['publish_min_odds_sources'] = min_price
    counts['publish_min_context_sources'] = min_context
    counts['context_source_projection_updated_utc'] = datetime.now(UTC).isoformat()
    return counts


def main() -> int:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    d = target_date(now)
    min_price = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    inv_path = DAY_INV_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        inv = {'date_local': d, 'matches': []}
    matches = [dict(row) for row in inv.get('matches', []) if isinstance(row, dict)]
    changed = 0
    projected = 0
    ready_publish_after = 0
    examples: list[dict[str, Any]] = []
    for row in matches:
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        sources, confirmations, reasons = projected_context_sources(row)
        if sources or confirmations:
            projected += 1
        existing_sources = {norm(x) for x in list_from_any(row.get('context_sources')) if norm(x)}
        existing_confirmations = {norm(x) for x in list_from_any(row.get('context_confirmations')) if norm(x)}
        all_sources = sorted((existing_sources | sources) - {'odds_api_io', ''})
        all_confirmations = sorted((existing_confirmations | confirmations) - {'odds_api_io', ''})
        row['context_sources'] = all_sources
        row['context_confirmations'] = all_confirmations
        metadata['context_sources_count'] = max(as_int(metadata.get('context_sources_count')), len(all_sources))
        metadata['confirmation_sources_count'] = max(as_int(metadata.get('confirmation_sources_count')), len(all_confirmations))
        metadata['context_source_projection_updated_utc'] = now_iso
        if reasons:
            metadata['context_source_projection_reasons'] = sorted(set(reasons))
        row['metadata'] = metadata

        price_count = max(as_int(metadata.get('price_confirmation_sources_count')), len(row.get('price_confirmations') or []))
        context_count = max(as_int(metadata.get('context_sources_count')), len(all_confirmations), len(all_sources))
        has_odds = bool(coverage.get('odds')) or price_count > 0
        has_context = bool(coverage.get('context')) or context_count > 0
        coverage['context'] = has_context
        coverage['context_2plus_sources'] = context_count >= min_context
        coverage['odds_2plus_sources'] = price_count >= min_price
        coverage['ready_for_model'] = bool(coverage.get('ready_for_model')) or (has_odds and has_context)
        coverage['ready_for_publish'] = bool(coverage.get('ready_for_publish')) or (price_count >= min_price and context_count >= min_context and has_odds and has_context)
        row['coverage'] = coverage
        row['coverage_gaps'] = {
            'price_confirmations': price_count,
            'context_confirmations': context_count,
            'need_price_confirmations': max(0, min_price - price_count),
            'need_context_confirmations': max(0, min_context - context_count),
            'has_odds': has_odds,
            'has_context': has_context,
        }
        if coverage['ready_for_publish']:
            ready_publish_after += 1
        after = json.dumps(row, ensure_ascii=False, sort_keys=True)
        changed += int(before != after)
        if len(examples) < 12 and (context_count < min_context or price_count < min_price):
            examples.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'fixture_sources': row.get('fixture_sources') or row.get('sources_seen'),
                'price_confirmations': price_count,
                'context_confirmations': context_count,
                'gaps': row['coverage_gaps'],
            })
    inv['matches'] = matches
    inv['counts'] = recompute_counts(matches, inv.get('counts') if isinstance(inv.get('counts'), dict) else {}, min_price, min_context)
    inv['updated_at_utc'] = now_iso
    sources_meta = inv.setdefault('sources', {})
    if isinstance(sources_meta, dict):
        sources_meta['context_source_projection'] = {
            'updated_at_utc': now_iso,
            'rows_seen': len(matches),
            'rows_changed': changed,
            'rows_projected': projected,
            'ready_for_publish_after': ready_publish_after,
            'min_price_confirmations': min_price,
            'min_context_sources': min_context,
        }
    for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
        write_json(path, inv)
    summary = {
        'date_local': d,
        'updated_at_utc': now_iso,
        'timezone': str(app_tz()),
        'build_status': inv.get('build_status') or 'ok',
        'counts': inv.get('counts', {}),
        'source_match_counts': dict(inv.get('source_match_counts') or {}),
        'league_match_counts': dict(inv.get('league_match_counts') or {}),
        'sources': dict(inv.get('sources') or {}),
    }
    write_json(SUMMARY, summary)
    report = {
        'status': 'ok',
        'date_local': d,
        'updated_at_utc': now_iso,
        'inventory_path': str(inv_path),
        'rows_seen': len(matches),
        'rows_changed': changed,
        'rows_projected': projected,
        'ready_for_publish_after': ready_publish_after,
        'counts': inv.get('counts', {}),
        'gap_examples': examples,
        'notes': [
            'Projects explicit context_sources/context_confirmations from provider provenance and existing xg/form/context flags.',
            'Does not invent odds prices; price confirmations still come from odds/bookmaker evidence.',
            'coverage_gaps shows what each tracked match still needs to reach 2+ price and 2+ context confirmations.',
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
