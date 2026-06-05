from __future__ import annotations

"""Backfill bookmaker-quorum evidence from raw odds/API artifacts into coverage truth.

This script is API-free. It does not create prices or candidates. It only scans
already saved runtime artifacts for real bookmaker offers, groups them by
match + market side + point, and copies the strongest bookmaker count back into
`.data/day_inventory` and `latest-day-inventory-coverage-truth.json`.

Why it exists: odds-api.io provider stats can say "2+ bookmakers: 80", while the
normalized inventory sees only 30-35. That means raw offers were fetched, but the
bookmaker quorum did not survive the merge into frozen inventory coverage truth.
"""

import csv
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
REPORT_PATH = EXPORT_DIR / 'latest-odds-api-bookmaker-quorum-mapping-backfill.json'
TRUTH_JSON = EXPORT_DIR / 'latest-day-inventory-coverage-truth.json'
TRUTH_CSV = EXPORT_DIR / 'latest-day-inventory-coverage-truth.csv'
SUMMARY_JSON = EXPORT_DIR / 'latest-day-inventory-summary.json'
HIGHWATER_PATH = DAY_INV_DIR / 'coverage_truth_highwater.json'

SYNTHETIC_BOOK_TOKENS = {
    'consensus', 'bzzoiroconsensus', 'bzzoiro-consensus', 'sstatsconsensus',
    'market', 'model', 'ensemble', 'average', 'avg', 'median', 'harizon'
}
LIVE_ODDS_SOURCE = 'odds_api_io'


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw in (None, ''):
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(timezone.utc).astimezone(app_tz()).date().isoformat()


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        x = float(str(value).strip().replace(',', '.'))
        return x if math.isfinite(x) else default
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        if isinstance(value, bool):
            return int(value)
        return int(float(str(value)))
    except Exception:
        return default


def norm_text(value: Any) -> str:
    return re.sub(r'[^a-z0-9а-яё]+', ' ', str(value or '').strip().lower()).strip()


def norm_key(value: Any) -> str:
    text = norm_text(value)
    stop = {'fc','fk','sc','cf','ac','club','cd','de','da','del','if','bk','afc','ii','b','res','reserve','u19','u20','u21'}
    parts = [p for p in text.split() if p and p not in stop]
    return ' '.join(parts)


def compact(value: Any) -> str:
    return re.sub(r'[^a-z0-9а-яё]+', '_', str(value or '').strip().lower()).strip('_')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})', text)
        if m:
            d, mo, y, h, mi = map(int, m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=app_tz()).astimezone(timezone.utc)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def local_date(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        text = str(value or '')
        m = re.search(r'(20\d\d-\d\d-\d\d)', text)
        return m.group(1) if m else ''
    return dt.astimezone(app_tz()).date().isoformat()


def date_from_row(row: dict[str, Any]) -> str:
    for key in ('date_local','kickoff_utc','commence_time','start_time','kickoff','kickoff_local','event_time','starts_at'):
        if row.get(key):
            if key == 'date_local' and re.match(r'^20\d\d-\d\d-\d\d$', str(row.get(key))):
                return str(row.get(key))
            d = local_date(row.get(key))
            if d:
                return d
    text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    m = re.search(r'(20\d\d-\d\d-\d\d)', text)
    return m.group(1) if m else ''


def row_key_variants(row: dict[str, Any]) -> set[str]:
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    ids: set[str] = set()
    for key in ('match_key','canonical_match_id','event_id','id','fixture_id','game_id'):
        for src in (row, md):
            val = src.get(key) if isinstance(src, dict) else None
            if str(val or '').strip():
                ids.add(str(val).strip())
                ids.add(f'{LIVE_ODDS_SOURCE}:{val}')
    source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    provider_ids = md.get('provider_source_ids') if isinstance(md.get('provider_source_ids'), dict) else {}
    for src in (source_ids, provider_ids):
        for k, v in src.items():
            if str(k).lower().startswith(('odds', 'odds_api')) and str(v or '').strip():
                ids.add(str(v).strip())
                ids.add(f'{LIVE_ODDS_SOURCE}:{v}')
    d = date_from_row(row)
    home = norm_key(row.get('home_team') or row.get('home') or md.get('home_team'))
    away = norm_key(row.get('away_team') or row.get('away') or md.get('away_team'))
    if d and home and away:
        a, b = sorted([home, away])
        ids.update({
            f'{d}|{home}|{away}', f'{d}|{away}|{home}', f'{d}|{a}|{b}',
            f'soccer|{compact(home)}|{compact(away)}|{d}',
            f'soccer|{compact(away)}|{compact(home)}|{d}',
            f'soccer|{compact(a)}|{compact(b)}|{d}',
        })
    return {x for x in ids if x}


def walk_dicts(obj: Any, limit: int, state: dict[str, int]):
    if state['seen'] >= limit:
        return
    if isinstance(obj, dict):
        state['seen'] += 1
        yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from walk_dicts(value, limit, state)
                if state['seen'] >= limit:
                    return
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from walk_dicts(item, limit, state)
                if state['seen'] >= limit:
                    return


def source_is_odds_api(row: dict[str, Any], source_path: str) -> bool:
    blob = ' '.join(str(row.get(k) or '') for k in ('source','provider','provider_id','api','source_id','market_source'))
    blob += ' ' + source_path
    return any(token in blob.lower() for token in ('odds_api_io', 'odds-api.io', 'oddsapiio', 'odds_api'))


def bookmaker(row: dict[str, Any]) -> str:
    for key in ('bookmaker','book','sportsbook','bookie','provider_bookmaker','selected_bookmaker'):
        val = row.get(key)
        if str(val or '').strip():
            b = norm_text(val).replace(' ', '')
            if b and not any(tok in b for tok in SYNTHETIC_BOOK_TOKENS):
                return b
    return ''


def selection_side(row: dict[str, Any]) -> str:
    text = ' '.join(str(row.get(k) or '') for k in ('selection','selection_key','name','label','outcome','side','market_name','market_key','path','source_path'))
    text = text.lower()
    if any(x in text for x in ('under', 'меньше', 'тотал меньше', 'тм')):
        return 'under'
    if any(x in text for x in ('over', 'больше', 'тотал больше', 'тб')):
        return 'over'
    return ''


def point_value(row: dict[str, Any]) -> str:
    for key in ('point','line','handicap','total','threshold'):
        x = as_float(row.get(key), None)
        if x is not None and 0.0 < x < 20.0:
            return f'{round(x, 3):g}'
    text = ' '.join(str(row.get(k) or '') for k in ('selection','selection_key','market_name','market_key','path','source_path','name'))
    m = re.search(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', text)
    if m:
        x = as_float(m.group(1), None)
        if x is not None and 0.0 < x < 20.0:
            return f'{round(x, 3):g}'
    return ''


def family(row: dict[str, Any]) -> str:
    text = ' '.join(str(row.get(k) or '') for k in ('family','market_family','market','market_key','market_name','path','source_path')).lower()
    if any(x in text for x in ('total','over_under','overunder','goals')):
        return 'totals'
    if any(x in text for x in ('spread','handicap','asian')):
        return 'spreads'
    return ''


def is_offer_like(row: dict[str, Any]) -> bool:
    price = as_float(row.get('price') or row.get('odds') or row.get('decimal_odds') or row.get('decimal'), None)
    return price is not None and 1.01 <= price <= 50.0 and bool(bookmaker(row))


def extract_offers_from_file(path: Path, max_dicts: int) -> list[dict[str, Any]]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return []
        if path.stat().st_size > env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_FILE_BYTES', 8_000_000):
            return []
        if path.suffix.lower() not in {'.json'}:
            return []
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    offers: list[dict[str, Any]] = []
    state = {'seen': 0}
    for row in walk_dicts(payload, max_dicts, state):
        if not isinstance(row, dict) or not is_offer_like(row):
            continue
        if not source_is_odds_api(row, str(path)):
            # raw_bucket_offers often omit provider on the offer, but parent file name may be generic;
            # still accept real bookmaker rows only when they carry a match identity.
            if not any(row.get(k) for k in ('match_key','canonical_match_id','event_id','fixture_id','game_id','home_team','home')):
                continue
        offers.append(row)
    return offers


def candidate_files() -> list[Path]:
    roots = [EXPORT_DIR, ROOT / 'artifacts' / 'run-bot', ROOT / '.data' / 'cache', DAY_INV_DIR]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*.json'):
            name = path.name.lower()
            full = str(path).lower()
            if any(tok in name or tok in full for tok in ('odds', 'offer', 'candidate', 'pick', 'coverage', 'inventory', 'progressive')):
                out.append(path)
    # Prefer files that likely contain offers.
    out.sort(key=lambda p: (0 if any(t in p.name.lower() for t in ('odds', 'offer', 'candidate', 'pick')) else 1, str(p)))
    return out[: env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_FILES', 80)]


def build_inventory_index(rows: list[dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for row in rows:
        canonical = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if not canonical:
            keys = row_key_variants(row)
            canonical = sorted(keys)[0] if keys else ''
        if not canonical:
            continue
        for key in row_key_variants(row):
            index[key] = canonical
    return index


def load_inventory_rows(date_local: str) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    inv_path = DAY_INV_DIR / f'{date_local}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict) or not isinstance(inv.get('matches'), list):
        inv_path = DAY_INV_DIR / 'latest.json'
        inv = load_json(inv_path, {})
    rows = [r for r in inv.get('matches', []) if isinstance(r, dict)] if isinstance(inv, dict) else []
    return inv if isinstance(inv, dict) else {}, rows, inv_path


def load_truth_rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    truth = load_json(TRUTH_JSON, {})
    rows = [r for r in truth.get('rows', []) if isinstance(r, dict)] if isinstance(truth, dict) else []
    return truth if isinstance(truth, dict) else {}, rows


def write_truth_csv(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = sorted({k for r in rows for k in r.keys() if not isinstance(r.get(k), (dict, list))})
    # Keep important columns first.
    first = ['match_key','kickoff_utc','league_name','home_team','away_team','odds_sources_count','odds_sources','price_confirmations','books_count','context_sources_count','context_sources','has_odds','has_context','ready_for_model','ready_for_publish','tier_a_coverage_ready','tier_b_coverage_ready','bookmaker_quorum_backfilled']
    fields = [x for x in first if x in {k for r in rows for k in r.keys()}] + [x for x in fields if x not in first]
    TRUTH_CSV.parent.mkdir(parents=True, exist_ok=True)
    with TRUTH_CSV.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = {}
            for key in fields:
                val = row.get(key)
                if isinstance(val, list):
                    val = '|'.join(str(x) for x in val)
                elif isinstance(val, dict):
                    val = json.dumps(val, ensure_ascii=False, sort_keys=True)
                flat[key] = val
            writer.writerow(flat)


def apply_to_row(row: dict[str, Any], book_count: int, min_books: int) -> bool:
    before = json.dumps({k: row.get(k) for k in ('books_count','price_confirmations','has_odds','odds_sources','odds_sources_count')}, ensure_ascii=False, sort_keys=True)
    row['books_count'] = max(as_int(row.get('books_count')), book_count)
    row['price_confirmations'] = max(as_int(row.get('price_confirmations')), row['books_count'])
    osrc = list_from_any(row.get('odds_sources'))
    if LIVE_ODDS_SOURCE not in {str(x) for x in osrc}:
        osrc.append(LIVE_ODDS_SOURCE)
    row['odds_sources'] = sorted(set(osrc))
    row['odds_sources_count'] = max(as_int(row.get('odds_sources_count')), len(row['odds_sources']))
    row['has_odds'] = True
    if row['books_count'] >= min_books:
        row['bookmaker_quorum_backfilled'] = True
        row['bookmaker_quorum_contract_ready'] = bool(row.get('has_context')) and as_int(row.get('context_sources_count')) >= 1
    after = json.dumps({k: row.get(k) for k in ('books_count','price_confirmations','has_odds','odds_sources','odds_sources_count')}, ensure_ascii=False, sort_keys=True)
    return before != after


def recompute_counts(rows: list[dict[str, Any]], min_books: int, min_context: int) -> dict[str, int]:
    return {
        'matches_total': len(rows),
        'matches_with_odds': sum(1 for r in rows if r.get('has_odds') or as_int(r.get('books_count')) > 0),
        'matches_with_context': sum(1 for r in rows if r.get('has_context')),
        'matches_with_2plus_price_confirmations': sum(1 for r in rows if max(as_int(r.get('books_count')), as_int(r.get('price_confirmations'))) >= min_books),
        'matches_with_2plus_odds_sources': sum(1 for r in rows if as_int(r.get('odds_sources_count')) >= 2),
        'matches_with_2plus_context_sources': sum(1 for r in rows if as_int(r.get('context_sources_count')) >= min_context),
        'matches_ready_for_model': sum(1 for r in rows if r.get('ready_for_model') or ((r.get('has_odds') or as_int(r.get('books_count')) > 0) and r.get('has_context'))),
    }


def main() -> int:
    enabled = env_bool('ODDS_API_BOOKMAKER_QUORUM_BACKFILL_ENABLED', True)
    date_local = target_date()
    report: dict[str, Any] = {
        'enabled': enabled,
        'policy': 'map_raw_odds_api_real_bookmaker_quorum_to_frozen_inventory',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'date_local': date_local,
        'files_scanned': 0,
        'offer_rows_seen': 0,
        'mapped_matches': 0,
        'unmatched_match_groups': 0,
        'changed_truth_rows': 0,
        'changed_inventory_rows': 0,
        'status': 'disabled' if not enabled else 'ok',
    }
    if not enabled:
        write_json(REPORT_PATH, report)
        return 0

    min_books = max(2, env_int('PUBLISH_MIN_BOOKS', 2))
    min_context = max(2, env_int('PUBLISH_MIN_CONTEXT_SOURCES', env_int('MIN_CONTEXT_SOURCES_PUBLISH', 2)))
    inv, inv_rows, inv_path = load_inventory_rows(date_local)
    truth, truth_rows = load_truth_rows()
    inventory_index = build_inventory_index(inv_rows + truth_rows)
    if not inventory_index:
        report['status'] = 'no_inventory_index'
        write_json(REPORT_PATH, report)
        return 0

    # raw_group[canonical_match][market_bucket] = set(bookmakers)
    raw_group: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    unmatched_groups: set[str] = set()
    max_dicts = env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_DICTS_PER_FILE', 120000)
    for path in candidate_files():
        report['files_scanned'] += 1
        offers = extract_offers_from_file(path, max_dicts=max_dicts)
        if not offers:
            continue
        for offer in offers:
            report['offer_rows_seen'] += 1
            side = selection_side(offer)
            pt = point_value(offer)
            fam = family(offer)
            book = bookmaker(offer)
            if not side or not pt or not book:
                continue
            keys = row_key_variants(offer)
            canonical = ''
            for key in keys:
                if key in inventory_index:
                    canonical = inventory_index[key]
                    break
            if not canonical:
                unmatched_groups.add('|'.join(sorted(keys))[:200] or json.dumps({k: offer.get(k) for k in ('home_team','away_team','match_key','event_id')}, ensure_ascii=False))
                continue
            bucket = f'{fam or "market"}|{side}|{pt}'
            raw_group[canonical][bucket].add(book)

    best_by_match: dict[str, int] = {}
    for canonical, buckets in raw_group.items():
        best = max((len(books) for books in buckets.values()), default=0)
        if best >= min_books:
            best_by_match[canonical] = best

    truth_by_key = {str(r.get('match_key') or r.get('canonical_match_id') or '').strip(): r for r in truth_rows if str(r.get('match_key') or r.get('canonical_match_id') or '').strip()}
    for row in truth_rows:
        canonical = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
        if canonical in best_by_match:
            if apply_to_row(row, best_by_match[canonical], min_books):
                report['changed_truth_rows'] += 1

    for row in inv_rows:
        # match any variant through canonical index
        canonical = ''
        for key in row_key_variants(row):
            if key in inventory_index:
                canonical = inventory_index[key]
                break
        if canonical in best_by_match:
            if apply_to_row(row, best_by_match[canonical], min_books):
                report['changed_inventory_rows'] += 1
            cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
            cov['books_count'] = max(as_int(cov.get('books_count')), best_by_match[canonical])
            cov['price_confirmation_sources_count'] = max(as_int(cov.get('price_confirmation_sources_count')), best_by_match[canonical])
            cov['odds'] = True
            cov['bookmaker_quorum_backfilled'] = True
            row['coverage'] = cov

    report['mapped_matches'] = len(best_by_match)
    report['unmatched_match_groups'] = len(unmatched_groups)
    report['sample_mapped'] = sorted(list(best_by_match.items()), key=lambda x: -x[1])[:20]
    report['sample_unmatched'] = list(sorted(unmatched_groups))[:20]

    # Recompute truth counts lightly; the normalizer will run next and do the full contract recalc.
    if truth_rows:
        counts = recompute_counts(truth_rows, min_books, min_context)
        truth['counts'] = dict(truth.get('counts') or {}) | counts
        truth['bookmaker_quorum_backfill'] = {k: v for k, v in report.items() if k.startswith(('mapped','changed','offer','files','unmatched','status','policy'))}
        write_json(TRUTH_JSON, truth)
        write_truth_csv(truth_rows)
        summary = load_json(SUMMARY_JSON, {})
        if isinstance(summary, dict):
            summary['coverage_truth_counts'] = truth['counts']
            summary['bookmaker_quorum_backfill'] = truth['bookmaker_quorum_backfill']
            write_json(SUMMARY_JSON, summary)
    if isinstance(inv, dict) and inv_rows:
        inv['bookmaker_quorum_backfill_updated_at_utc'] = report['created_at_utc']
        inv['bookmaker_quorum_backfill'] = {k: report[k] for k in ('mapped_matches','changed_inventory_rows','offer_rows_seen','files_scanned')}
        for path in {inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json'}:
            write_json(path, inv)
    # Patch highwater evidence so later runs do not lose the backfilled book counts.
    high = load_json(HIGHWATER_PATH, {})
    if isinstance(high, dict) and isinstance(high.get('rows'), dict):
        changed = 0
        for key, row in high['rows'].items():
            if key in best_by_match and isinstance(row, dict):
                old = as_int(row.get('books_count'))
                row['books_count'] = max(old, best_by_match[key])
                row['price_confirmations'] = max(as_int(row.get('price_confirmations')), row['books_count'])
                row['has_odds'] = True
                changed += int(row['books_count'] != old)
        if changed:
            high['bookmaker_quorum_backfill_updated_at_utc'] = report['created_at_utc']
            write_json(HIGHWATER_PATH, high)
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
