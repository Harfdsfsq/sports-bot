from __future__ import annotations

"""Backfill bookmaker-quorum evidence from odds-api.io offer snapshots into coverage truth.

API-free safety layer. It does not create odds, candidates, or predictions. It only
copies evidence that already exists in saved odds-api.io Offer rows into frozen day
inventory / coverage truth so the bookmaker-quorum publication contract can see it.

This version reads the provider-written `latest-odds-api-io-offer-snapshot.json`
directly and bypasses the generic file-size scanner for that known large file.
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
ODDS_API_SNAPSHOT_PATH = EXPORT_DIR / 'latest-odds-api-io-offer-snapshot.json'
TRUTH_JSON = EXPORT_DIR / 'latest-day-inventory-coverage-truth.json'
TRUTH_CSV = EXPORT_DIR / 'latest-day-inventory-coverage-truth.csv'
SUMMARY_JSON = EXPORT_DIR / 'latest-day-inventory-summary.json'
HIGHWATER_PATH = DAY_INV_DIR / 'coverage_truth_highwater.json'

SYNTHETIC_BOOK_TOKENS = {
    'consensus', 'bzzoiroconsensus', 'bzzoiro-consensus', 'sstatsconsensus',
    'market', 'model', 'ensemble', 'average', 'avg', 'median', 'harizon'
}
LIVE_ODDS_SOURCE = 'odds_api_io'
STOP_TOKENS = {
    'fc', 'fk', 'sc', 'cf', 'ac', 'club', 'cd', 'de', 'da', 'del', 'if', 'bk', 'afc',
    'ii', 'b', 'res', 'reserve', 'u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'ec', 'sp', 'rs', 'pr'
}


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
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def norm_text(value: Any) -> str:
    return re.sub(r'[^a-z0-9а-яё]+', ' ', str(value or '').strip().lower()).strip()


def token_set(value: Any) -> set[str]:
    text = norm_text(value)
    return {p for p in text.split() if p and p not in STOP_TOKENS}


def norm_key(value: Any) -> str:
    return ' '.join(sorted(token_set(value)))


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
    for key in ('date_local', 'kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'kickoff_local', 'event_time', 'starts_at'):
        if row.get(key):
            if key == 'date_local' and re.match(r'^20\d\d-\d\d-\d\d$', str(row.get(key))):
                return str(row.get(key))
            d = local_date(row.get(key))
            if d:
                return d
    text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    m = re.search(r'(20\d\d-\d\d-\d\d)', text)
    return m.group(1) if m else ''


def kickoff_dt(row: dict[str, Any]) -> datetime | None:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'kickoff_local', 'event_time', 'starts_at'):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def row_key_variants(row: dict[str, Any]) -> set[str]:
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    ids: set[str] = set()
    for key in ('match_key', 'canonical_match_id', 'event_id', 'id', 'fixture_id', 'game_id', 'source_event_id', 'odds_api_io_id'):
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
    home_raw = row.get('home_team') or row.get('home') or md.get('home_team')
    away_raw = row.get('away_team') or row.get('away') or md.get('away_team')
    home = norm_key(home_raw)
    away = norm_key(away_raw)
    if d and home and away:
        a, b = sorted([home, away])
        ids.update({
            f'{d}|{home}|{away}', f'{d}|{away}|{home}', f'{d}|{a}|{b}',
            f'soccer|{compact(home)}|{compact(away)}|{d}',
            f'soccer|{compact(away)}|{compact(home)}|{d}',
            f'soccer|{compact(a)}|{compact(b)}|{d}',
        })
    return {x for x in ids if x}


def source_is_odds_api(row: dict[str, Any], source_path: str) -> bool:
    blob = ' '.join(str(row.get(k) or '') for k in ('source', 'provider', 'provider_id', 'api', 'source_id', 'market_source'))
    blob += ' ' + source_path
    return any(token in blob.lower() for token in ('odds_api_io', 'odds-api.io', 'oddsapiio', 'odds_api'))


def bookmaker(row: dict[str, Any]) -> str:
    for key in ('bookmaker', 'book', 'sportsbook', 'bookie', 'provider_bookmaker', 'selected_bookmaker'):
        val = row.get(key)
        if str(val or '').strip():
            b = norm_text(val).replace(' ', '')
            if b and not any(tok in b for tok in SYNTHETIC_BOOK_TOKENS):
                return b
    return ''


def selection_side(row: dict[str, Any]) -> str:
    text = ' '.join(str(row.get(k) or '') for k in ('selection', 'selection_key', 'name', 'label', 'outcome', 'side', 'market_name', 'market_key', 'path', 'source_path'))
    text = text.lower()
    if any(x in text for x in ('under', 'меньше', 'тотал меньше', 'тм')):
        return 'under'
    if any(x in text for x in ('over', 'больше', 'тотал больше', 'тб')):
        return 'over'
    return ''


def point_value(row: dict[str, Any]) -> str:
    for key in ('point', 'line', 'handicap', 'total', 'threshold'):
        x = as_float(row.get(key), None)
        if x is not None and 0.0 < x < 20.0:
            return f'{round(x, 3):g}'
    text = ' '.join(str(row.get(k) or '') for k in ('selection', 'selection_key', 'market_name', 'market_key', 'path', 'source_path', 'name'))
    m = re.search(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', text)
    if m:
        x = as_float(m.group(1), None)
        if x is not None and 0.0 < x < 20.0:
            return f'{round(x, 3):g}'
    return ''


def family(row: dict[str, Any]) -> str:
    text = ' '.join(str(row.get(k) or '') for k in ('family', 'market_family', 'market', 'market_key', 'market_name', 'path', 'source_path')).lower()
    if any(x in text for x in ('total', 'over_under', 'overunder', 'goals')):
        return 'totals'
    if any(x in text for x in ('spread', 'handicap', 'asian')):
        return 'spreads'
    return ''


def is_offer_like(row: dict[str, Any]) -> bool:
    price = as_float(row.get('price') or row.get('odds') or row.get('decimal_odds') or row.get('decimal'), None)
    return price is not None and 1.01 <= price <= 50.0 and bool(bookmaker(row))


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


def extract_offers_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    offers = snapshot.get('offers')
    if isinstance(offers, list):
        return [row for row in offers if isinstance(row, dict) and is_offer_like(row)]
    return []


def extract_offers_from_file(path: Path, max_dicts: int) -> list[dict[str, Any]]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return []
        # The known provider snapshot can be large. Read it directly; all other files keep the size guard.
        if path != ODDS_API_SNAPSHOT_PATH and path.stat().st_size > env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_FILE_BYTES', 8_000_000):
            return []
        if path.suffix.lower() not in {'.json'}:
            return []
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    if path == ODDS_API_SNAPSHOT_PATH and isinstance(payload, dict):
        return extract_offers_from_snapshot(payload)
    offers: list[dict[str, Any]] = []
    state = {'seen': 0}
    for row in walk_dicts(payload, max_dicts, state):
        if not isinstance(row, dict) or not is_offer_like(row):
            continue
        if not source_is_odds_api(row, str(path)):
            if not any(row.get(k) for k in ('match_key', 'canonical_match_id', 'event_id', 'fixture_id', 'game_id', 'home_team', 'home')):
                continue
        offers.append(row)
    return offers


def candidate_files() -> list[Path]:
    roots = [EXPORT_DIR, ROOT / 'artifacts' / 'run-bot', ROOT / '.data' / 'cache', DAY_INV_DIR]
    out: list[Path] = []
    if ODDS_API_SNAPSHOT_PATH.exists():
        out.append(ODDS_API_SNAPSHOT_PATH)
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*.json'):
            if path == ODDS_API_SNAPSHOT_PATH and path in out:
                continue
            name = path.name.lower()
            full = str(path).lower()
            if any(tok in name or tok in full for tok in ('odds', 'offer', 'candidate', 'pick', 'coverage', 'inventory', 'progressive')):
                out.append(path)
    out.sort(key=lambda p: (0 if p == ODDS_API_SNAPSHOT_PATH else 1 if any(t in p.name.lower() for t in ('odds', 'offer', 'candidate', 'pick')) else 2, str(p)))
    return out[: env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_FILES', 120)]


def canonical_for_row(row: dict[str, Any]) -> str:
    return str(row.get('match_key') or row.get('canonical_match_id') or '').strip()


def build_inventory_index(rows: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    index: dict[str, str] = {}
    fuzzy_rows: list[dict[str, Any]] = []
    for row in rows:
        canonical = canonical_for_row(row)
        if not canonical:
            keys = row_key_variants(row)
            canonical = sorted(keys)[0] if keys else ''
        if not canonical:
            continue
        for key in row_key_variants(row):
            index[key] = canonical
        fuzzy_rows.append({
            'canonical': canonical,
            'row': row,
            'home_tokens': token_set(row.get('home_team') or row.get('home')),
            'away_tokens': token_set(row.get('away_team') or row.get('away')),
            'date': date_from_row(row),
            'kickoff': kickoff_dt(row),
        })
    return index, fuzzy_rows


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


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def pair_score(oh: set[str], oa: set[str], rh: set[str], ra: set[str]) -> float:
    direct = (jaccard(oh, rh) + jaccard(oa, ra)) / 2.0
    reverse = (jaccard(oh, ra) + jaccard(oa, rh)) / 2.0
    return max(direct, reverse)


def fuzzy_canonical(offer: dict[str, Any], fuzzy_rows: list[dict[str, Any]]) -> str:
    oh = token_set(offer.get('home_team') or offer.get('home'))
    oa = token_set(offer.get('away_team') or offer.get('away'))
    if not oh or not oa:
        return ''
    odt = kickoff_dt(offer)
    od = date_from_row(offer)
    best_score = 0.0
    best = ''
    for item in fuzzy_rows:
        rh = item.get('home_tokens') or set()
        ra = item.get('away_tokens') or set()
        if not rh or not ra:
            continue
        score = pair_score(oh, oa, rh, ra)
        if score < 0.58:
            continue
        rdt = item.get('kickoff')
        if odt is not None and rdt is not None:
            diff_h = abs((odt - rdt).total_seconds()) / 3600.0
            if diff_h > 18:
                continue
            score += max(0.0, 0.25 - diff_h / 72.0)
        elif od and item.get('date') and od != item.get('date'):
            # UTC/local date can differ by one day, but do not allow completely unrelated dates.
            try:
                od_date = datetime.fromisoformat(od).date()
                rd_date = datetime.fromisoformat(str(item.get('date'))).date()
                if abs((od_date - rd_date).days) > 1:
                    continue
            except Exception:
                continue
        if score > best_score:
            best_score = score
            best = str(item.get('canonical') or '')
    return best if best_score >= 0.62 else ''


def write_truth_csv(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = sorted({k for r in rows for k in r.keys() if not isinstance(r.get(k), (dict, list))})
    first = ['match_key', 'kickoff_utc', 'league_name', 'home_team', 'away_team', 'odds_sources_count', 'odds_sources', 'price_confirmations', 'books_count', 'context_sources_count', 'context_sources', 'has_odds', 'has_context', 'ready_for_model', 'ready_for_publish', 'tier_a_coverage_ready', 'tier_b_coverage_ready', 'bookmaker_quorum_backfilled']
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
    before = json.dumps({k: row.get(k) for k in ('books_count', 'price_confirmations', 'has_odds', 'odds_sources', 'odds_sources_count')}, ensure_ascii=False, sort_keys=True)
    row['books_count'] = max(as_int(row.get('books_count')), book_count)
    row['price_confirmations'] = max(as_int(row.get('price_confirmations')), row['books_count'])
    osrc = list_from_any(row.get('odds_sources'))
    if LIVE_ODDS_SOURCE not in {str(x) for x in osrc}:
        osrc.append(LIVE_ODDS_SOURCE)
    row['odds_sources'] = sorted(set(osrc))
    row['odds_sources_count'] = max(as_int(row.get('odds_sources_count')), len(row['odds_sources']))
    row['has_odds'] = True
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    md['books_count'] = max(as_int(md.get('books_count')), row['books_count'])
    md['price_confirmation_sources_count'] = max(as_int(md.get('price_confirmation_sources_count')), row['books_count'])
    md['odds_api_bookmaker_quorum_backfilled'] = True
    row['metadata'] = md
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    cov['books_count'] = max(as_int(cov.get('books_count')), row['books_count'])
    cov['price_confirmation_sources_count'] = max(as_int(cov.get('price_confirmation_sources_count')), row['books_count'])
    cov['odds'] = True
    if row['books_count'] >= min_books:
        row['bookmaker_quorum_backfilled'] = True
        row['bookmaker_quorum_contract_ready'] = bool(row.get('has_context')) and as_int(row.get('context_sources_count')) >= 1
        cov['bookmaker_quorum_backfilled'] = True
    row['coverage'] = cov
    after = json.dumps({k: row.get(k) for k in ('books_count', 'price_confirmations', 'has_odds', 'odds_sources', 'odds_sources_count')}, ensure_ascii=False, sort_keys=True)
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
        'policy': 'map_odds_api_offer_snapshot_real_bookmaker_quorum_to_frozen_inventory_v2',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'date_local': date_local,
        'files_scanned': 0,
        'offer_rows_seen': 0,
        'offer_rows_from_snapshot': 0,
        'offer_rows_from_generic_files': 0,
        'snapshot_file_present': ODDS_API_SNAPSHOT_PATH.exists(),
        'snapshot_rows_count': 0,
        'snapshot_matches_count': 0,
        'mapped_matches': 0,
        'mapped_by_exact_key': 0,
        'mapped_by_fuzzy_teams_kickoff': 0,
        'unmatched_match_groups': 0,
        'changed_truth_rows': 0,
        'changed_inventory_rows': 0,
        'status': 'disabled' if not enabled else 'ok',
    }
    snapshot: dict[str, Any] = {}
    if ODDS_API_SNAPSHOT_PATH.exists():
        loaded = load_json(ODDS_API_SNAPSHOT_PATH, {})
        snapshot = loaded if isinstance(loaded, dict) else {}
        report['snapshot_rows_count'] = as_int(snapshot.get('rows_count')) or len(snapshot.get('offers') or [])
        report['snapshot_matches_count'] = as_int(snapshot.get('matches_count')) or len(snapshot.get('by_match') or [])
    if not enabled:
        write_json(REPORT_PATH, report)
        return 0

    min_books = max(2, env_int('PUBLISH_MIN_BOOKS', 2))
    min_context = max(2, env_int('PUBLISH_MIN_CONTEXT_SOURCES', env_int('MIN_CONTEXT_SOURCES_PUBLISH', 2)))
    inv, inv_rows, inv_path = load_inventory_rows(date_local)
    truth, truth_rows = load_truth_rows()
    inventory_index, fuzzy_rows = build_inventory_index(inv_rows + truth_rows)
    if not inventory_index:
        report['status'] = 'no_inventory_index'
        write_json(REPORT_PATH, report)
        return 0

    raw_group: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    unmatched_groups: set[str] = set()
    max_dicts = env_int('BOOKMAKER_QUORUM_BACKFILL_MAX_DICTS_PER_FILE', 120000)
    seen_offer_fingerprints: set[str] = set()

    for path in candidate_files():
        report['files_scanned'] += 1
        offers = extract_offers_from_file(path, max_dicts=max_dicts)
        if not offers:
            continue
        is_snapshot_file = path == ODDS_API_SNAPSHOT_PATH
        for offer in offers:
            fp = '|'.join(str(offer.get(k) or '') for k in ('match_key', 'event_id', 'bookmaker', 'family', 'selection', 'point', 'price'))
            if fp in seen_offer_fingerprints:
                continue
            seen_offer_fingerprints.add(fp)
            report['offer_rows_seen'] += 1
            report['offer_rows_from_snapshot' if is_snapshot_file else 'offer_rows_from_generic_files'] += 1
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
                    report['mapped_by_exact_key'] += 1
                    break
            if not canonical:
                canonical = fuzzy_canonical(offer, fuzzy_rows)
                if canonical:
                    report['mapped_by_fuzzy_teams_kickoff'] += 1
            if not canonical:
                unmatched_groups.add('|'.join(sorted(keys))[:220] or json.dumps({k: offer.get(k) for k in ('home_team', 'away_team', 'match_key', 'event_id')}, ensure_ascii=False))
                continue
            bucket = f'{fam or "market"}|{side}|{pt}'
            raw_group[canonical][bucket].add(book)

    best_by_match: dict[str, int] = {}
    for canonical, buckets in raw_group.items():
        best = max((len(books) for books in buckets.values()), default=0)
        if best >= min_books:
            best_by_match[canonical] = best

    for row in truth_rows:
        canonical = canonical_for_row(row)
        if canonical in best_by_match:
            if apply_to_row(row, best_by_match[canonical], min_books):
                report['changed_truth_rows'] += 1

    for row in inv_rows:
        canonical = ''
        for key in row_key_variants(row):
            if key in inventory_index:
                canonical = inventory_index[key]
                break
        if canonical in best_by_match:
            if apply_to_row(row, best_by_match[canonical], min_books):
                report['changed_inventory_rows'] += 1

    report['mapped_matches'] = len(best_by_match)
    report['unmatched_match_groups'] = len(unmatched_groups)
    report['sample_mapped'] = sorted(list(best_by_match.items()), key=lambda x: -x[1])[:20]
    report['sample_unmatched'] = list(sorted(unmatched_groups))[:20]

    if truth_rows:
        counts = recompute_counts(truth_rows, min_books, min_context)
        truth['counts'] = dict(truth.get('counts') or {}) | counts
        truth['bookmaker_quorum_backfill'] = {
            k: v for k, v in report.items()
            if k.startswith(('mapped', 'changed', 'offer', 'files', 'unmatched', 'status', 'policy', 'snapshot'))
        }
        write_json(TRUTH_JSON, truth)
        write_truth_csv(truth_rows)
        summary = load_json(SUMMARY_JSON, {})
        if isinstance(summary, dict):
            summary['coverage_truth_counts'] = truth['counts']
            summary['bookmaker_quorum_backfill'] = truth['bookmaker_quorum_backfill']
            write_json(SUMMARY_JSON, summary)
    if isinstance(inv, dict) and inv_rows:
        inv['bookmaker_quorum_backfill_updated_at_utc'] = report['created_at_utc']
        inv['bookmaker_quorum_backfill'] = {
            k: report[k]
            for k in ('mapped_matches', 'changed_inventory_rows', 'offer_rows_seen', 'offer_rows_from_snapshot', 'files_scanned')
        }
        for path in {inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json'}:
            write_json(path, inv)
    high = load_json(HIGHWATER_PATH, {})
    if isinstance(high, dict) and isinstance(high.get('rows'), dict):
        changed = 0
        for key, row in high['rows'].items():
            if key in best_by_match and isinstance(row, dict):
                old = as_int(row.get('books_count'))
                apply_to_row(row, best_by_match[key], min_books)
                changed += int(as_int(row.get('books_count')) != old)
        if changed:
            high['bookmaker_quorum_backfill_updated_at_utc'] = report['created_at_utc']
            write_json(HIGHWATER_PATH, high)
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
