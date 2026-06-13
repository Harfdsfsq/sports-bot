from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-fresh-b-cover-diagnostics.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def point(value: Any) -> str:
    if value in (None, '', 'null'):
        return ''
    try:
        f = float(str(value).replace(',', '.'))
        return str(int(f)) if f.is_integer() else f'{f:g}'
    except Exception:
        return norm(value)


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('rows', 'matches', 'items', 'data'):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def as_int(value: Any) -> int:
    try:
        if isinstance(value, list):
            return len(set(norm(v) for v in value if norm(v)))
        if isinstance(value, dict):
            return len(value)
        return int(float(value or 0))
    except Exception:
        return 0


def books_count(row: dict[str, Any]) -> int:
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('books_count', 'bookmakers_count', 'price_confirmations', 'priced_books_count'):
        n = as_int(row.get(key)) or as_int(ss.get(key))
        if n:
            return n
    for key in ('books', 'bookmakers', 'priced_books'):
        val = row.get(key) or ss.get(key)
        n = as_int(val)
        if n:
            return n
    return 1 if (row.get('bookmaker') or ss.get('bookmaker')) else 0


def context_count(row: dict[str, Any]) -> int:
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('context_sources_count', 'confirmation_sources_count', 'sources_count'):
        n = as_int(row.get(key)) or as_int(ss.get(key))
        if n:
            return n
    for key in ('context_sources', 'confirmation_sources', 'providers'):
        n = as_int(row.get(key) or ss.get(key))
        if n:
            return n
    return 1 if (row.get('context') or ss.get('context')) else 0


def match_key(row: dict[str, Any]) -> str:
    explicit = norm(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key'))
    if explicit:
        return explicit
    home = norm(row.get('home_team') or row.get('home'))
    away = norm(row.get('away_team') or row.get('away'))
    date = str(row.get('date') or row.get('commence_time') or row.get('kickoff') or '')[:10]
    return '|'.join(x for x in (home, away, date) if x)


def offer_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    family = norm(row.get('family') or row.get('market_family'))
    selection = norm(row.get('selection_key') or row.get('selection'))
    if any(x in selection for x in ('under', 'меньше', 'тм')):
        selection = 'under'
    elif any(x in selection for x in ('over', 'больше', 'тб')):
        selection = 'over'
    return (match_key(row), family, selection, point(row.get('point') or row.get('line') or row.get('handicap')))


def collect_offer_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths = [
        ROOT / '.data' / 'exports' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / '.data' / 'exports' / 'latest-line-snapshots.json',
        ROOT / 'artifacts' / 'run-bot' / 'exports' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / 'artifacts' / 'run-bot' / 'exports' / 'latest-line-snapshots.json',
    ]
    for path in paths:
        payload = load_json(path, None)
        for row in rows_from_payload(payload):
            row = dict(row)
            row['_source_path'] = str(path)
            if match_key(row):
                rows.append(row)
    return rows


def main() -> int:
    inv_path = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-truth.json'
    inv_rows = rows_from_payload(load_json(inv_path, {}))
    if not inv_rows:
        inv_path = ROOT / '.data' / 'day_inventory' / 'latest.json'
        inv_rows = rows_from_payload(load_json(inv_path, {}))

    b_rows = [r for r in inv_rows if books_count(r) >= 1 and context_count(r) >= 1]
    offers = collect_offer_rows()
    offers_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    offers_by_bucket: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for offer in offers:
        mk = match_key(offer)
        if not mk:
            continue
        offers_by_match[mk].append(offer)
        offers_by_bucket[offer_key(offer)].append(offer)

    no_match = 0
    has_match = 0
    current_total_buckets = 0
    bucket_book_counts = Counter()
    for row in b_rows:
        mk = match_key(row)
        if mk in offers_by_match:
            has_match += 1
        else:
            no_match += 1

    for key, bucket in offers_by_bucket.items():
        if key[1] in {'totals', 'spreads'}:
            books = {norm(x.get('bookmaker') or x.get('book')) for x in bucket if norm(x.get('bookmaker') or x.get('book'))}
            current_total_buckets += 1
            bucket_book_counts[str(len(books))] += 1

    promotion = load_json(ROOT / '.data' / 'exports' / 'latest-b-cover-value-promotion.json', {})
    report = load_json(ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json', {})
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'inventory_path': str(inv_path),
        'inventory_rows': len(inv_rows),
        'b_cover_rows': len(b_rows),
        'offer_rows_seen': len(offers),
        'b_cover_with_any_current_offer_match': has_match,
        'b_cover_without_current_offer_match': no_match,
        'current_market_buckets_totals_spreads': current_total_buckets,
        'current_market_bucket_book_count_histogram': dict(bucket_book_counts),
        'promotion_reason_counts': promotion.get('reason_counts') if isinstance(promotion, dict) else {},
        'promotion_promoted_count': promotion.get('promoted_count') if isinstance(promotion, dict) else None,
        'fallback_candidates_seen': report.get('candidates_seen') if isinstance(report, dict) else None,
        'fallback_status': report.get('status') if isinstance(report, dict) else None,
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
