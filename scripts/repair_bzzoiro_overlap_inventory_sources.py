from __future__ import annotations

"""Promote exact Bzzoiro same-bucket overlap into inventory source evidence.

The bridge file already proves that Bzzoiro has a totals offer for the same match
and same market bucket as a reference odds snapshot.  This script does not create
candidates or relax publication guards; it only prevents A-tier coverage from
undercounting Bzzoiro as the second independent line source when the overlap was
already observed.
"""

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
DAY_INV = ROOT / '.data' / 'day_inventory'
OUT = EXPORT / 'latest-bzzoiro-overlap-inventory-source-repair.json'
UTC = timezone.utc


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    return ' '.join(re.sub(r'[^a-z0-9а-я]+', ' ', str(value or '').lower().replace('ё', 'е')).split())


def date_of(row: dict[str, Any]) -> str:
    for key in ('date', 'kickoff_utc', 'commence_time', 'start_time', 'match_key', 'canonical_match_id'):
        m = re.search(r'20\d{2}-\d{2}-\d{2}', str(row.get(key) or ''))
        if m:
            return m.group(0)
    return ''


def aliases(row: dict[str, Any]) -> set[str]:
    date = date_of(row)
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name'))
    raw = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
    out = {norm(raw), raw}
    if date and home and away:
        out.update({
            f'{date}|{home}|{away}',
            f'{date}|{away}|{home}',
            f'soccer|{home}|{away}|{date}',
            f'soccer|{away}|{home}|{date}',
            f'teams:{date}|{home}|{away}',
            f'teams:{date}|{away}|{home}',
        })
    return {item for item in out if item and item.strip('|')}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(str(value).replace(',', '.'))
        return f if math.isfinite(f) else default
    except Exception:
        return default


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit[:10]
    try:
        tz = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        tz = timezone.utc
    return datetime.now(UTC).astimezone(tz).date().isoformat()


def main() -> int:
    now = datetime.now(UTC).isoformat()
    inv_path = DAY_INV / f'{target_date()}.json'
    inv = load(inv_path, {})
    matches = inv.get('matches') if isinstance(inv, dict) else None
    if not isinstance(matches, list):
        payload = {'status': 'no_inventory', 'inventory_path': str(inv_path), 'updated_at_utc': now}
        dump(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    overlap = load(EXPORT / 'latest-bzzoiro-overlap-offers.json', {})
    rows = overlap.get('rows') if isinstance(overlap, dict) else []
    rows = [row for row in rows if isinstance(row, dict) and bool(row.get('overlap_same_bucket_found')) and as_float(row.get('price') or row.get('odds')) > 1.01]

    index: dict[str, list[dict[str, Any]]] = {}
    for row in matches:
        if not isinstance(row, dict):
            continue
        for alias in aliases(row):
            index.setdefault(alias, []).append(row)

    changed_rows = 0
    matched_offers = 0
    examples: list[dict[str, Any]] = []
    touched: set[int] = set()
    for offer in rows:
        targets: list[dict[str, Any]] = []
        for alias in aliases(offer):
            targets.extend(index.get(alias, []))
        seen_ids: set[int] = set()
        for row in targets:
            rid = id(row)
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            odds_sources = set(list_from_any(row.get('odds_sources')))
            line_sources = set(list_from_any(row.get('line_sources')))
            books = set(list_from_any(row.get('books')))
            price_confirmations = set(list_from_any(row.get('price_confirmations')))
            before = (tuple(sorted(odds_sources)), tuple(sorted(line_sources)), tuple(sorted(books)), tuple(sorted(price_confirmations)))
            odds_sources.add('bzzoiro')
            line_sources.add('bzzoiro')
            book = str(offer.get('bookmaker') or offer.get('book') or 'Bzzoiro').strip() or 'Bzzoiro'
            books.add(book)
            price_confirmations.add(f"bzzoiro:{offer.get('family') or 'totals'}:{offer.get('selection') or ''}:{offer.get('point') or ''}")
            after = (tuple(sorted(odds_sources)), tuple(sorted(line_sources)), tuple(sorted(books)), tuple(sorted(price_confirmations)))
            if after != before:
                row['odds_sources'] = sorted(odds_sources)
                row['line_sources'] = sorted(line_sources)
                row['books'] = sorted(books)
                row['price_confirmations'] = sorted(price_confirmations)
                md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
                md['odds_sources_count'] = max(int(md.get('odds_sources_count') or 0), len(odds_sources | line_sources))
                md['independent_odds_sources_count'] = max(int(md.get('independent_odds_sources_count') or 0), len(odds_sources | line_sources))
                md['books_count'] = max(int(md.get('books_count') or 0), len(books))
                md['price_confirmation_sources_count'] = max(int(md.get('price_confirmation_sources_count') or 0), len(price_confirmations), len(books))
                md['bzzoiro_overlap_source_repair_utc'] = now
                row['metadata'] = md
                cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
                cov['odds'] = True
                cov['odds_2plus_sources'] = len(odds_sources | line_sources) >= 2
                row['coverage'] = cov
                touched.add(rid)
                if len(examples) < 10:
                    examples.append({'match_key': row.get('match_key'), 'home_team': row.get('home_team'), 'away_team': row.get('away_team'), 'odds_sources': sorted(odds_sources | line_sources)})
        matched_offers += int(bool(targets))

    changed_rows = len(touched)
    if changed_rows:
        inv['updated_at_utc'] = now
        sources = inv.setdefault('sources', {})
        if isinstance(sources, dict):
            sources['bzzoiro_overlap_inventory_source_repair'] = {'updated_at_utc': now, 'changed_rows': changed_rows, 'matched_offers': matched_offers}
        for path in (inv_path, DAY_INV / 'latest.json', DAY_INV / 'current.json', DAY_INV / 'today.json'):
            dump(path, inv)

    payload = {'status': 'ok', 'updated_at_utc': now, 'overlap_rows_seen': len(rows), 'matched_offers': matched_offers, 'changed_rows': changed_rows, 'examples': examples}
    dump(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
