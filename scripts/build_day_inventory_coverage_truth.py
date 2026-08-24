from __future__ import annotations

"""Build an auditable per-match coverage truth table for the day inventory.

This report separates public A-tier readiness from B-tier watchlist readiness.
Public publication is A-tier only: 2 independent odds/line sources, 2 bookmaker
price confirmations and 2 context sources. B-tier remains watchlist-only and is
tracked separately for diagnostics and promotion.

This file is not only a report. ``build_b_cover_candidate_gap_report.py`` prefers
it as the promotion inventory because it carries per-row books and context, so
every field dropped here is a field the model cannot use. ``row_truth`` builds a
new dict, therefore row-level enrichment (provider xG, goal rates, offer
counters, canonical ids) has to be passed through explicitly.
"""

import csv
import json
import math
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
OUT_JSON = EXPORT_DIR / 'latest-day-inventory-coverage-truth.json'
OUT_CSV = EXPORT_DIR / 'latest-day-inventory-coverage-truth.csv'
SUMMARY_PATH = EXPORT_DIR / 'latest-day-inventory-summary.json'

LIVE_ODDS_SOURCES = {'odds_api_io', 'bzzoiro', 'sportlogic'}
CORE_CONTEXT_SOURCES = {'sstats', 'bzzoiro', 'clubelo', 'sportlogic', 'football_data', 'thesportsdb', 'api_football', 'espn', 'openligadb', 'openfootball', 'futrixmetrics'}
NON_CONTEXT = {'', 'ensemble', 'market', 'market_signal', 'line_history', 'odds_api_io', 'xg_model_context', 'form_context', 'fixture', 'alias', 'proxy', 'inventory', 'day_inventory'}

# Row fields the downstream model and promotion read directly. They are not
# coverage counters, so they are copied through untouched; dropping them is what
# made every promoted candidate arrive with provider_xg_source null while the
# inventory itself carried real goal rates.
PASSTHROUGH_FIELDS = (
    'canonical_match_id',
    'sstats_game_id',
    'commence_time',
    'kickoff_local',
    'expected_home',
    'expected_away',
    'sstats_expected_home',
    'sstats_expected_away',
    'sstats_xg_source',
    'sstats_lambda_home',
    'sstats_lambda_away',
    'sstats_form_games',
    'sstats_offer_count',
    'sstats_offer_books',
    'sstats_offer_points',
    'books',
    'bookmakers',
)

EMPTY_VALUES = (None, '', [], {}, ())

# Labels SStats deep enrichment writes when it did not extract anything itself.
NON_PROVIDER_XG_SOURCES = {'', 'existing_inventory', 'missing'}


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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        if isinstance(value, bool):
            return None
        number = float(str(value).replace(',', '.'))
        if math.isfinite(number):
            return number
    except Exception:
        return None
    return None


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
        'bsd_sports': 'bzzoiro',
        'sport_logic': 'sportlogic',
        'sportlogic_io': 'sportlogic',
        'sstats_form': 'sstats',
        'sstats_net': 'sstats',
        'football_data_org': 'football_data',
        'sportsdb': 'thesportsdb',
        'the_sports_db': 'thesportsdb',
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def unique_norm(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = norm(value)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def metadata(row: dict[str, Any]) -> dict[str, Any]:
    return row.get('metadata') if isinstance(row.get('metadata'), dict) else {}


def coverage(row: dict[str, Any]) -> dict[str, Any]:
    return row.get('coverage') if isinstance(row.get('coverage'), dict) else {}


def containers(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = [row]
    for key in ('coverage', 'metadata', 'source_summary', 'day_inventory_coverage', 'progressive_coverage'):
        value = row.get(key)
        if isinstance(value, dict):
            out.append(value)
            nested = value.get('day_inventory_coverage')
            if isinstance(nested, dict):
                out.append(nested)
    return out


def count_from_metadata(row: dict[str, Any], *keys: str) -> int:
    best = 0
    for container in containers(row):
        for key in keys:
            best = max(best, as_int(container.get(key)))
    return best


def provider_xg_source(row: dict[str, Any]) -> str:
    """The provider-extraction label for this row's xG, or '' if there is none.

    Same contract as ``build_b_cover_candidate_gap_report.provider_xg_source`` on
    purpose: market-implied and proxy xG are not evidence about the teams, and the
    market-implied backfill is recognisable by its exact home == away split.
    """
    for container in containers(row):
        source = norm(container.get('sstats_xg_source'))
        if source in NON_PROVIDER_XG_SOURCES:
            continue
        if 'market' in source or 'proxy' in source:
            continue
        home = as_float(container.get('sstats_expected_home'))
        away = as_float(container.get('sstats_expected_away'))
        if home is None or away is None:
            continue
        if abs(home - away) <= 1e-6:
            continue
        return source
    return ''


def price_backed_extra_odds_sources(row: dict[str, Any]) -> list[str]:
    """Vendors that quoted a price for this row outside the live-odds runners.

    SStats totals offers are real bookmaker prices from a second vendor, so they
    are a genuine independent price source. They are counted only when the row
    carries actual offers: a context label alone must never create a price
    source, otherwise a single-source pick can wear an A label.
    """
    out: list[str] = []
    for container in containers(row):
        offers = as_int(container.get('sstats_offer_count'))
        books = as_int(container.get('sstats_offer_books'))
        if offers >= 1 and books >= 1:
            out.append('sstats')
            break
    return out


def odds_sources(row: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for box in containers(row):
        values += list_from_any(box.get('odds_sources')) + list_from_any(box.get('line_sources')) + list_from_any(box.get('verified_odds_sources'))
    sources = unique_norm(values)
    out = [x for x in sources if x in LIVE_ODDS_SOURCES]
    for extra in price_backed_extra_odds_sources(row):
        if extra not in out:
            out.append(extra)
    return sorted(out)


def context_sources(row: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for box in containers(row):
        for key in ('context_sources', 'context_confirmations', 'verified_context_sources', 'all_context_sources', 'core_context_sources', 'confirmation_sources', 'runtime_context_bridge_sources'):
            values += list_from_any(box.get(key))
        for flag, src in (('bzzoiro_has_context_hint', 'bzzoiro'), ('sstats_has_context_hint', 'sstats'), ('sportlogic_context', 'sportlogic')):
            if box.get(flag):
                values.append(src)
    cleaned: set[str] = set()
    for item in unique_norm(values):
        if item.startswith('provider_'):
            item = item.removeprefix('provider_')
        if item in NON_CONTEXT or re.match(r'^context_(source|confirmation)_\d+$', item) or item.startswith('legacy_context_'):
            continue
        if item in CORE_CONTEXT_SOURCES:
            cleaned.add(item)
    return sorted(cleaned)


def price_confirmations(row: dict[str, Any]) -> int:
    return max(
        count_from_metadata(row, 'price_confirmation_sources_count', 'price_sources_count', 'books_count', 'bookmaker_count', 'latest_books_max'),
        len(list_from_any(row.get('price_confirmations'))),
        len(list_from_any(row.get('books'))),
        len(list_from_any(metadata(row).get('verified_bookmakers'))),
    )


def passthrough_enrichment(row: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """Copy model-relevant row fields into the truth row without overriding it.

    A computed coverage value always wins: this only fills fields the coverage
    table does not own, so the promotion can still read goal rates, offer
    counters and canonical ids from the file it selects.
    """
    for key in PASSTHROUGH_FIELDS:
        if key in truth and truth.get(key) not in EMPTY_VALUES:
            continue
        value = row.get(key)
        if value in EMPTY_VALUES:
            continue
        truth[key] = value
    return truth


def row_truth(row: dict[str, Any], min_odds: int, min_context: int) -> dict[str, Any]:
    cov = coverage(row)
    osrc = odds_sources(row)
    csrc = context_sources(row)
    pc = price_confirmations(row)
    cc = len(csrc)
    has_odds = bool(cov.get('odds')) or pc > 0 or bool(osrc)
    has_context = bool(cov.get('context')) or cc > 0

    a_min_odds = max(2, as_int(os.getenv('PUBLISH_TIER_A_MIN_ODDS_SOURCES'), min_odds))
    a_min_context = max(2, as_int(os.getenv('PUBLISH_TIER_A_MIN_CONTEXT_SOURCES'), min_context))
    a_min_price = max(2, as_int(os.getenv('PUBLISH_TIER_A_MIN_BOOKS'), min_odds))
    b_min_odds = max(1, as_int(os.getenv('PUBLISH_TIER_B_MIN_ODDS_SOURCES'), 1))
    b_min_context = max(1, as_int(os.getenv('PUBLISH_TIER_B_MIN_CONTEXT_SOURCES'), 1))
    b_min_price = max(2, as_int(os.getenv('PUBLISH_TIER_B_MIN_BOOKS'), 2))

    tier_a_missing: list[str] = []
    if pc < a_min_price:
        tier_a_missing.append('price_confirmations')
    if len(osrc) < a_min_odds:
        tier_a_missing.append('independent_odds_sources')
    if cc < a_min_context:
        tier_a_missing.append('context_sources')

    tier_b_missing: list[str] = []
    if pc < b_min_price:
        tier_b_missing.append('price_confirmations')
    if len(osrc) < b_min_odds:
        tier_b_missing.append('independent_odds_sources')
    if cc < b_min_context:
        tier_b_missing.append('context_sources')

    tier_a_ready = has_odds and has_context and not tier_a_missing
    tier_b_ready = has_odds and has_context and not tier_b_missing
    xg_source = provider_xg_source(row)
    truth = {
        'match_key': row.get('match_key') or row.get('canonical_match_id') or '',
        'kickoff_utc': row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local') or '',
        'league_name': row.get('league_name') or '',
        'home_team': row.get('home_team') or '',
        'away_team': row.get('away_team') or '',
        'odds_sources': osrc,
        'odds_sources_count': len(osrc),
        'price_confirmations': pc,
        'books_count': max(count_from_metadata(row, 'books_count', 'bookmaker_count'), len(list_from_any(row.get('books')))),
        'context_sources': csrc,
        'context_sources_count': cc,
        'has_odds': has_odds,
        'has_context': has_context,
        'provider_xg_source': xg_source,
        'has_provider_xg': bool(xg_source),
        'ready_for_model': bool(cov.get('ready_for_model')) or tier_b_ready or (has_odds and has_context),
        'ready_for_publish': tier_a_ready,
        'tier_a_coverage_ready': tier_a_ready,
        'tier_b_coverage_ready': tier_b_ready,
        'tier_b_watch_only': True,
        'tier_b_bookmaker_quorum_ready': pc >= b_min_price,
        'tier_b_confirmation_mode': 'watchlist_only' if tier_b_ready else 'none',
        'need_price_confirmations': max(0, a_min_price - pc),
        'need_odds_sources': max(0, a_min_odds - len(osrc)),
        'need_context_sources': max(0, a_min_context - cc),
        'tier_b_need_price_confirmations': max(0, b_min_price - pc),
        'tier_b_need_odds_sources': max(0, b_min_odds - len(osrc)),
        'tier_b_need_context_sources': max(0, b_min_context - cc),
        'missing': tier_a_missing,
        'tier_a_missing': tier_a_missing,
        'tier_b_missing': tier_b_missing,
    }
    return passthrough_enrichment(row, truth)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        'match_key', 'kickoff_utc', 'league_name', 'home_team', 'away_team',
        'odds_sources_count', 'odds_sources', 'price_confirmations', 'books_count',
        'context_sources_count', 'context_sources', 'has_odds', 'has_context',
        'provider_xg_source', 'has_provider_xg',
        'ready_for_model', 'ready_for_publish', 'tier_a_coverage_ready',
        'tier_b_coverage_ready', 'tier_b_watch_only', 'need_price_confirmations',
        'need_odds_sources', 'need_context_sources', 'tier_b_need_price_confirmations',
        'tier_b_need_odds_sources', 'tier_b_need_context_sources', 'missing',
        'tier_a_missing', 'tier_b_missing',
    ]
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ('odds_sources', 'context_sources', 'missing', 'tier_a_missing', 'tier_b_missing'):
                flat[key] = '|'.join(str(x) for x in flat.get(key) or [])
            writer.writerow({key: flat.get(key, '') for key in fields})


def main() -> int:
    now = datetime.now(UTC).isoformat()
    d = target_date()
    min_odds = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    inv_path = DAY_INV_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    matches = [row for row in inv.get('matches', []) if isinstance(row, dict)] if isinstance(inv, dict) else []
    rows = [row_truth(row, min_odds, min_context) for row in matches]
    rows.sort(key=lambda x: (str(x.get('kickoff_utc') or ''), str(x.get('league_name') or ''), str(x.get('home_team') or '')))

    counts = {
        'matches_total': len(rows),
        'matches_with_odds': sum(1 for r in rows if r['has_odds']),
        'matches_with_context': sum(1 for r in rows if r['has_context']),
        'matches_with_2plus_price_confirmations': sum(1 for r in rows if r['price_confirmations'] >= 2),
        'matches_with_2plus_odds_sources': sum(1 for r in rows if r['odds_sources_count'] >= 2),
        'matches_with_2plus_context_sources': sum(1 for r in rows if r['context_sources_count'] >= 2),
        'matches_with_provider_xg': sum(1 for r in rows if r.get('has_provider_xg')),
        'matches_with_sstats_price_source': sum(1 for r in rows if 'sstats' in (r.get('odds_sources') or [])),
        'matches_ready_for_model': sum(1 for r in rows if r['ready_for_model']),
        'matches_ready_for_publish': sum(1 for r in rows if r['ready_for_publish']),
        'matches_a_tier_coverage_ready': sum(1 for r in rows if r['tier_a_coverage_ready']),
        'matches_b_tier_watch_ready': sum(1 for r in rows if r['tier_b_coverage_ready']),
        'matches_b_tier_watch_only_not_a_tier': sum(1 for r in rows if r['tier_b_coverage_ready'] and not r['tier_a_coverage_ready']),
    }
    counts['matches_missing_price_2plus'] = max(0, len(rows) - counts['matches_with_2plus_price_confirmations'])
    counts['matches_missing_odds_source_2plus'] = max(0, len(rows) - counts['matches_with_2plus_odds_sources'])
    counts['matches_missing_context_2plus'] = max(0, len(rows) - counts['matches_with_2plus_context_sources'])

    gap_examples = [r for r in rows if r['missing']][:25]
    payload = {
        'status': 'ok',
        'date_local': d,
        'updated_at_utc': now,
        'inventory_path': str(inv_path),
        'public_publication_tier': 'A-only',
        'b_tier_mode': 'watchlist_only',
        'min_odds_sources': min_odds,
        'min_context_sources': min_context,
        'counts': counts,
        'gap_examples': gap_examples,
        'rows': rows,
        'notes': [
            'odds_sources_count is independent live provider count only: odds_api_io, bzzoiro, sportlogic, plus sstats when the row carries real SStats offers.',
            'sstats is added as a price source only from sstats_offer_count/sstats_offer_books, never from a context label.',
            'price_confirmations is bookmaker/line depth and is tracked separately from provider independence.',
            'context_sources_count reads verified context evidence from row, metadata, coverage and runtime bridge fields; fixture-id, alias and proxy are not counted.',
            'ready_for_publish is A-tier only: 2+ price confirmations, 2+ independent odds sources, and 2+ context sources.',
            'tier_b_coverage_ready is watchlist-only and does not mean public publication is allowed.',
            'row-level enrichment (provider xG, SStats goal rates, form games, offer counters, sstats_game_id, canonical_match_id, book list) is passed through unchanged: the B-cover promotion selects this file, so a dropped field silently disables the xG model.',
            'provider_xg_source follows the same rule as the promotion: existing_inventory, missing, market-implied and proxy labels are not provider xG, and an exact home == away split is treated as market-implied.',
        ],
    }
    write_json(OUT_JSON, payload)
    write_csv(OUT_CSV, rows)

    summary = load_json(SUMMARY_PATH, {})
    if isinstance(summary, dict):
        sources = summary.setdefault('sources', {})
        if isinstance(sources, dict):
            sources['coverage_truth'] = {'updated_at_utc': now, 'json': str(OUT_JSON), 'csv': str(OUT_CSV), 'counts': counts}
        summary['coverage_truth_counts'] = counts
        summary['updated_at_utc'] = now
        write_json(SUMMARY_PATH, summary)
    print(json.dumps({k: payload[k] for k in ('status', 'date_local', 'updated_at_utc', 'counts', 'gap_examples')}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
