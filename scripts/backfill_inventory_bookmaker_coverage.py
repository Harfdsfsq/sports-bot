from __future__ import annotations

"""Backfill bookmaker/price coverage from raw odds artifacts into day inventory.

The Telegram reports showed raw odds-api.io had 40-67 same-side 2+ bookmaker
matches while normalized inventory only had 9-21.  This script repairs the
normalization layer by reading raw offer/candidate artifacts already produced by
runtime, matching them to frozen inventory rows, and copying bookmaker/price
counts into coverage/metadata.  It does not publish picks and does not change EV.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
CACHE_DAY_DIR = ROOT / '.data' / 'cache' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_PATH = EXPORT_DIR / 'latest-inventory-bookmaker-backfill.json'


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip()
            if text.endswith('Z'):
                text = text[:-1] + '+00:00'
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def target_date() -> str:
    return (os.getenv('DAY_INVENTORY_TARGET_DATE') or datetime.now(UTC).date().isoformat())[:10]


def row_date(row: dict[str, Any]) -> str:
    for key in ('commence_time', 'kickoff_utc', 'start_time', 'kickoff', 'date'):
        value = row.get(key)
        if not value:
            continue
        if key == 'date' and re.match(r'^20\d{2}-\d{2}-\d{2}$', str(value)[:10]):
            return str(value)[:10]
        dt = parse_dt(value)
        if dt:
            return dt.date().isoformat()
    for key in ('match_key', 'canonical_match_id', 'event_key'):
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', str(row.get(key) or ''))
        if m:
            return m.group(1)
    return ''


def team_pair(row: dict[str, Any]) -> tuple[str, str]:
    return (
        norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home')),
        norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away')),
    )


def key_variants(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ('canonical_match_id', 'match_key', 'event_key', 'id', 'event_id'):
        value = str(row.get(key) or '').strip()
        if value:
            out.add('id:' + norm(value))
    h, a = team_pair(row)
    d = row_date(row)
    if h and a and d:
        out.add(f'teams:{d}|{h}|{a}')
        out.add(f'teams_rev:{d}|{a}|{h}')
    return {x for x in out if x}


def as_price(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        f = float(str(value).replace(',', '.'))
        return f if f > 1.0 else None
    except Exception:
        return None


def bookmaker_of(row: dict[str, Any]) -> str:
    for key in ('bookmaker', 'bookmaker_slug', 'selected_bookmaker', 'selected_bookmaker_slug', 'book', 'sportsbook', 'provider_bookmaker'):
        value = norm(row.get(key))
        if value:
            return value
    return ''


def side_key(row: dict[str, Any]) -> str:
    fam = norm(row.get('family') or row.get('market_family') or row.get('market') or row.get('market_key'))
    selection = norm(row.get('selection_key') or row.get('selection') or row.get('outcome') or row.get('name'))
    point = row.get('point') or row.get('line') or row.get('handicap')
    try:
        point_s = f'{float(point):g}' if point not in (None, '') else ''
    except Exception:
        point_s = norm(point)
    team_side = norm(row.get('team_side') or row.get('side'))
    return '|'.join([fam, selection, point_s, team_side])


def iter_dicts(value: Any, depth: int = 0):
    if depth > 5:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from iter_dicts(v, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item, depth + 1)


def offer_like(row: dict[str, Any]) -> bool:
    has_price = any(as_price(row.get(k)) is not None for k in ('price', 'odds', 'decimal_odds', 'selected_odds'))
    has_book = bool(bookmaker_of(row))
    has_match = bool(key_variants(row) or (team_pair(row)[0] and team_pair(row)[1]))
    return has_price and has_book and has_match


def source_paths() -> list[Path]:
    names = [
        'latest-odds-api-io-offer-snapshot.json',
        'latest-odds-api-io-offers.json',
        'latest-bookmaker-quorum-normalizer.json',
        'latest-rescue-candidates.json',
        'latest-controlled-fallback-report.json',
        'latest-run-summary.json',
    ]
    paths = [EXPORT_DIR / name for name in names]
    paths.append(ROOT / '.logs' / 'debug-last-run.json')
    for root in (EXPORT_DIR, ROOT / 'artifacts' / 'run-bot'):
        if root.exists():
            paths.extend(sorted(root.glob('*odds*.json'))[:100])
            paths.extend(sorted(root.glob('*offer*.json'))[:100])
            paths.extend(sorted(root.glob('*candidate*.json'))[:100])
            paths.extend(sorted(root.glob('*/*.json'))[:200])
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        p = path.resolve()
        if p not in seen and path.exists():
            seen.add(p)
            out.append(path)
    return out


def collect_offer_groups(day: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    scanned = 0
    for path in source_paths():
        payload = load_json(path, None)
        if payload is None:
            continue
        accepted = 0
        for row in iter_dicts(payload):
            scanned += 1
            if not isinstance(row, dict) or not offer_like(row):
                continue
            d = row_date(row)
            if d and d != day:
                continue
            keys = key_variants(row)
            if not keys:
                h, a = team_pair(row)
                d = d or day
                if h and a:
                    keys = {f'teams:{d}|{h}|{a}'}
            book = bookmaker_of(row)
            price = as_price(row.get('price') or row.get('odds') or row.get('decimal_odds') or row.get('selected_odds'))
            if not book or price is None:
                continue
            skey = side_key(row)
            for key in keys:
                g = groups.setdefault(key, {'books': set(), 'prices': [], 'same_side': defaultdict(set), 'sample': []})
                g['books'].add(book)
                g['prices'].append(price)
                if skey.strip('|'):
                    g['same_side'][skey].add(book)
                if len(g['sample']) < 5:
                    g['sample'].append({
                        'bookmaker': book,
                        'price': price,
                        'family': row.get('family') or row.get('market_family') or row.get('market'),
                        'selection': row.get('selection') or row.get('selection_key') or row.get('outcome'),
                        'point': row.get('point') or row.get('line') or row.get('handicap'),
                    })
            accepted += 1
        if accepted:
            source_counts[str(path)] = accepted
    # convert sets/defaultdicts
    out: dict[str, dict[str, Any]] = {}
    for key, g in groups.items():
        same_side_counts = {s: len(bks) for s, bks in g['same_side'].items()}
        out[key] = {
            'books': sorted(g['books']),
            'books_count': len(g['books']),
            'prices_count': len(g['prices']),
            'same_side_2plus': sum(1 for c in same_side_counts.values() if c >= 2),
            'same_side_max_books': max(same_side_counts.values() or [0]),
            'sample': g['sample'],
        }
    return out, {'source_counts': source_counts, 'scanned_dicts': scanned}


def load_inventory(day: str) -> tuple[dict[str, Any], Path]:
    paths = [DAY_DIR / f'{day}.json', DAY_DIR / 'current.json', DAY_DIR / 'latest.json', CACHE_DAY_DIR / f'{day}.json', CACHE_DAY_DIR / 'today.json']
    best: dict[str, Any] = {'date_local': day, 'matches': [], 'counts': {}}
    best_path = DAY_DIR / f'{day}.json'
    best_count = -1
    for path in paths:
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
        if len(rows) > best_count:
            best = payload
            best_path = path
            best_count = len(rows)
    best.setdefault('matches', [])
    best.setdefault('counts', {})
    return best, best_path


def main() -> int:
    day = target_date()
    inventory, inventory_path = load_inventory(day)
    rows = inventory.get('matches') if isinstance(inventory.get('matches'), list) else []
    groups, diagnostics = collect_offer_groups(day)
    changed = 0
    matched = 0
    examples: list[dict[str, Any]] = []
    raw_2plus = sum(1 for g in groups.values() if int(g.get('same_side_max_books') or g.get('books_count') or 0) >= 2)
    normalized_2plus_before = 0
    normalized_2plus_after = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        cov = row.setdefault('coverage', {}) if isinstance(row.setdefault('coverage', {}), dict) else {}
        md = row.setdefault('metadata', {}) if isinstance(row.setdefault('metadata', {}), dict) else {}
        before_books = max(int(cov.get('books_count') or 0), int(md.get('books_count') or 0), len(cov.get('books') or []) if isinstance(cov.get('books'), list) else 0)
        if before_books >= 2:
            normalized_2plus_before += 1
        group = None
        for key in key_variants(row):
            if key in groups:
                group = groups[key]
                break
        if not group:
            continue
        matched += 1
        books = list(group.get('books') or [])
        same_side_max = int(group.get('same_side_max_books') or 0)
        books_count = max(len(books), same_side_max, before_books)
        if books_count > before_books:
            changed += 1
            cov['books_count'] = books_count
            cov['price_confirmations'] = max(int(cov.get('price_confirmations') or 0), books_count)
            cov['price_sources_count'] = max(int(cov.get('price_sources_count') or 0), books_count)
            cov['books'] = sorted(set([str(x) for x in (cov.get('books') if isinstance(cov.get('books'), list) else [])] + books))
            cov['odds'] = True
            md['books_count'] = books_count
            md['latest_books_max'] = max(int(md.get('latest_books_max') or 0), books_count)
            md['bookmaker_backfill_source'] = 'raw_offer_artifacts'
            md['bookmaker_backfill_updated_at_utc'] = datetime.now(UTC).isoformat()
            if len(examples) < 12:
                examples.append({
                    'match_key': row.get('match_key') or row.get('canonical_match_id'),
                    'home_team': row.get('home_team'),
                    'away_team': row.get('away_team'),
                    'before_books': before_books,
                    'after_books': books_count,
                    'sample': group.get('sample'),
                })
        if max(books_count, before_books) >= 2:
            normalized_2plus_after += 1

    inventory['matches'] = rows
    counts = inventory.setdefault('counts', {})
    counts['bookmaker_backfill_raw_2plus_matches'] = raw_2plus
    counts['bookmaker_backfill_normalized_2plus_before'] = normalized_2plus_before
    counts['bookmaker_backfill_normalized_2plus_after'] = normalized_2plus_after
    counts['bookmaker_backfill_mapping_gap_after'] = max(0, raw_2plus - normalized_2plus_after)
    counts['matches_with_2plus_price_confirmations'] = max(int(counts.get('matches_with_2plus_price_confirmations') or 0), normalized_2plus_after)
    inventory['bookmaker_backfill_updated_at_utc'] = datetime.now(UTC).isoformat()

    for path in (DAY_DIR / f'{day}.json', DAY_DIR / 'current.json', DAY_DIR / 'latest.json', DAY_DIR / 'today.json', CACHE_DAY_DIR / f'{day}.json', CACHE_DAY_DIR / 'today.json', CACHE_DAY_DIR / 'current.json', CACHE_DAY_DIR / 'latest.json'):
        write_json(path, inventory)

    report = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'inventory_path_used': str(inventory_path),
        'inventory_rows': len(rows),
        'raw_groups': len(groups),
        'raw_2plus_matches': raw_2plus,
        'matched_inventory_rows': matched,
        'changed_inventory_rows': changed,
        'normalized_2plus_before': normalized_2plus_before,
        'normalized_2plus_after': normalized_2plus_after,
        'mapping_gap_after': max(0, raw_2plus - normalized_2plus_after),
        'examples': examples,
        **diagnostics,
    }
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
