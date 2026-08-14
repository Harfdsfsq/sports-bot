from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT_OFFERS = EXPORT / 'latest-bzzoiro-overlap-offers.json'
OUT_REPORT = EXPORT / 'latest-bzzoiro-overlap-bridge.json'


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(v: Any) -> str:
    return ' '.join(re.sub(r'[^a-z0-9а-я]+', ' ', str(v or '').lower().replace('ё', 'е')).split())


def fnum(v: Any) -> float | None:
    try:
        f = float(str(v).replace(',', '.'))
        return f if math.isfinite(f) else None
    except Exception:
        return None


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or '').strip()
    if explicit:
        return explicit[:10]
    return datetime.now(timezone.utc).date().isoformat()


def date_of(key: str, row: dict[str, Any]) -> str:
    for v in (row.get('date'), row.get('kickoff_utc'), row.get('commence_time'), row.get('start_time'), row.get('kickoff'), key):
        m = re.search(r'20\d{2}-\d{2}-\d{2}', str(v or ''))
        if m:
            return m.group(0)
    return ''


def teams_from_key(key: str) -> tuple[str, str]:
    parts = [p for p in str(key or '').split('|') if p and not re.search(r'20\d{2}-\d{2}-\d{2}', p)]
    parts = [p for p in parts if norm(p) not in {'soccer', 'football', 'teams'}]
    return (parts[0], parts[1]) if len(parts) >= 2 else ('', '')


def aliases(date: str, home: Any, away: Any, raw: Any = '') -> set[str]:
    h, a = norm(home), norm(away)
    out = {norm(raw), str(raw or '').strip().lower()}
    if date and h and a:
        out.update({f'{date}|{h}|{a}', f'{date}|{a}|{h}', f'teams:{date}|{h}|{a}', f'teams:{date}|{a}|{h}', f'soccer|{h}|{a}|{date}', f'soccer|{a}|{h}|{date}'})
        out.add(f"pair|{'|'.join(sorted([re.sub(r'[^a-z0-9а-я]+', '', h), re.sub(r'[^a-z0-9а-я]+', '', a)]))}|{date}")
    return {x for x in out if x and x.strip('|')}


def point(h: dict[str, Any]) -> float | None:
    m = re.search(r'@([0-9]+(?:\.[0-9]+)?)', str(h.get('market_name') or ''))
    if m:
        return fnum(m.group(1))
    for k in ('point', 'line', 'handicap', 'total'):
        val = fnum(h.get(k))
        if val is not None:
            return val
    return None


def side(h: dict[str, Any]) -> str:
    text = str(h.get('market_name') or h.get('family') or h.get('market') or '').lower()
    raw = norm(h.get('selection') or h.get('outcome') or h.get('name'))
    if '.over@' in text or ' over@' in text or 'over' in raw or 'больше' in raw or 'тб' in raw:
        return 'over'
    if '.under@' in text or ' under@' in text or 'under' in raw or 'меньше' in raw or 'тм' in raw:
        return 'under'
    return raw


def bucket(row: dict[str, Any]) -> str:
    p = point(row)
    ps = '' if p is None else (str(int(p)) if p.is_integer() else f'{p:g}')
    return '|'.join(['totals', side(row), ps])


def _detail_offer_rows() -> tuple[list[dict[str, Any]], Counter]:
    """Materialize rows from targeted detail fetch into bridge-compatible offers.

    The previous bridge only read provider_odds_hints sidecars. After the event-id
    fix, Bzzoiro detail writes real Offer-like rows to latest-bzzoiro-odds.json;
    without reading that file the run can show detail offers > 0 while overlap and
    secondary promotion stay at zero.
    """
    out: list[dict[str, Any]] = []
    counts: Counter = Counter()
    for path in (EXPORT / 'latest-bzzoiro-odds.json', EXPORT / 'latest-bzzoiro-odds-raw.json'):
        payload = load(path, {})
        source_rows = rows(payload)
        for r in source_rows:
            if not isinstance(r, dict):
                continue
            fam = str(r.get('family') or r.get('market_family') or r.get('market') or '').lower()
            if fam not in {'totals', 'total', 'over_under'}:
                continue
            price = fnum(r.get('price') or r.get('odds') or r.get('decimal_odds'))
            p = point(r)
            sel = side(r)
            if price is None or price <= 1.01 or price > 50 or p is None or sel not in {'over', 'under'}:
                continue
            raw_key = str(r.get('match_key') or r.get('canonical_match_id') or r.get('source_match_key') or r.get('source_event_id') or '').strip()
            kh, ka = teams_from_key(raw_key)
            row = {
                'source': 'bzzoiro', 'provider': 'bzzoiro', 'bookmaker': r.get('bookmaker') or 'BzzoiroDetail', 'book': r.get('bookmaker') or 'BzzoiroDetail',
                'family': 'totals', 'market_family': 'totals', 'market': 'totals', 'market_key': 'totals',
                'selection': sel, 'selection_key': sel, 'outcome': sel,
                'point': p, 'line': p, 'price': price, 'odds': price, 'decimal_odds': price,
                'match_key': raw_key, 'canonical_match_id': raw_key,
                'home_team': r.get('home_team') or r.get('home') or kh,
                'away_team': r.get('away_team') or r.get('away') or ka,
                'date': date_of(raw_key, r),
                'commence_time': r.get('commence_time') or r.get('kickoff_utc') or r.get('date') or date_of(raw_key, r),
                'source_event_id': r.get('source_event_id') or r.get('event_id') or r.get('bzzoiro_event_id'),
                'bzzoiro_detail_offer_bridge': True,
            }
            out.append(row)
            counts[path.name] += 1
    return out, counts


def bzz_rows() -> tuple[list[dict[str, Any]], Counter]:
    out: list[dict[str, Any]] = []
    src_counts: Counter = Counter()
    detail_rows, detail_counts = _detail_offer_rows()
    out.extend(detail_rows)
    src_counts.update(detail_counts)
    for path in (EXPORT / 'latest-bzzoiro-v2-odds-hints-by-match.json', EXPORT / 'latest-bzzoiro-odds-hints-by-match.json'):
        payload = load(path, {})
        matches = payload.get('matches') if isinstance(payload, dict) else None
        if not isinstance(matches, dict):
            continue
        for raw_key, mp in matches.items():
            if not isinstance(mp, dict):
                continue
            kh, ka = teams_from_key(str(raw_key))
            home = mp.get('home') or mp.get('home_team') or kh
            away = mp.get('away') or mp.get('away_team') or ka
            date = date_of(str(raw_key), mp)
            details = mp.get('context_details') if isinstance(mp.get('context_details'), dict) else mp
            hints = details.get('provider_odds_hints') if isinstance(details, dict) else None
            if not isinstance(hints, list):
                continue
            for h in hints:
                if not isinstance(h, dict):
                    continue
                price = fnum(h.get('price') or h.get('odds') or h.get('decimal_odds'))
                p = point(h)
                sel = side(h)
                market_name = str(h.get('market_name') or '').lower()
                if price is None or p is None or sel not in {'over', 'under'} or price <= 1.01:
                    continue
                if '.line' in market_name or abs(price - p) < 1e-9:
                    continue
                row = {
                    'source': 'bzzoiro', 'provider': 'bzzoiro', 'bookmaker': 'Bzzoiro', 'book': 'Bzzoiro',
                    'family': 'totals', 'market_family': 'totals', 'market': 'totals', 'market_key': 'totals',
                    'selection': sel, 'selection_key': sel, 'outcome': sel,
                    'point': p, 'line': p, 'price': price, 'odds': price, 'decimal_odds': price,
                    'match_key': str(raw_key), 'canonical_match_id': str(raw_key),
                    'home_team': home, 'away_team': away, 'date': date,
                    'commence_time': mp.get('commence_time') or mp.get('kickoff_utc') or date,
                    'bzzoiro_offer_bridge': True,
                }
                out.append(row)
                src_counts[path.name] += 1
    seen, deduped = set(), []
    for r in out:
        sig = json.dumps([r.get('match_key'), r.get('source_event_id'), r.get('selection'), r.get('point'), r.get('price'), r.get('bookmaker')], sort_keys=True)
        if sig not in seen:
            seen.add(sig)
            deduped.append(r)
    return deduped, src_counts


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ('rows', 'items', 'data', 'matches', 'offers', 'snapshots', 'lines'):
            if isinstance(payload.get(k), list):
                return [x for x in payload[k] if isinstance(x, dict)]
    return []


def ref_index() -> dict[str, set[str]]:
    idx: dict[str, set[str]] = defaultdict(set)
    for path in (EXPORT / 'latest-odds-api-io-offer-snapshot.json', EXPORT / 'latest-line-snapshots.json', EXPORT / 'latest-consensus-lines.json', EXPORT / 'latest-matches.json'):
        for r in rows(load(path, [])):
            home = r.get('home_team') or r.get('home') or r.get('home_name')
            away = r.get('away_team') or r.get('away') or r.get('away_name')
            raw = r.get('match_key') or r.get('canonical_match_id') or ''
            d = date_of(str(raw), r)
            for a in aliases(d, home, away, raw):
                idx[a].add(bucket(r))
    return idx


def _row_aliases(r: dict[str, Any]) -> set[str]:
    key = str(r.get('match_key') or r.get('canonical_match_id') or '').strip()
    return aliases(r.get('date'), r.get('home_team'), r.get('away_team'), key)


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    day = target_date()
    out, source_counts = bzz_rows()
    idx = ref_index()
    match_overlap_rows = bucket_overlap_rows = 0
    unique_offer_matches: set[str] = set()
    unique_match_overlap: set[str] = set()
    unique_bucket_overlap: set[str] = set()
    target_date_rows = 0
    target_date_matches: set[str] = set()
    no_alias_rows = 0
    for r in out:
        key = str(r.get('match_key') or '').strip()
        if key:
            unique_offer_matches.add(key)
        if date_of(key, r) == day:
            target_date_rows += 1
            if key:
                target_date_matches.add(key)
        als = _row_aliases(r)
        if not als:
            no_alias_rows += 1
        b = bucket(r)
        has_match = any(a in idx for a in als)
        has_bucket = any(b in idx.get(a, set()) for a in als)
        r['overlap_match_alias_found'] = has_match
        r['overlap_same_bucket_found'] = has_bucket
        r['overlap_bucket_key'] = b
        match_overlap_rows += int(has_match)
        bucket_overlap_rows += int(has_bucket)
        if has_match and key:
            unique_match_overlap.add(key)
        if has_bucket and key:
            unique_bucket_overlap.add(key)
    report = {
        'status': 'ok', 'created_at_utc': now, 'target_date': day, 'offers_path': str(OUT_OFFERS),
        'bzzoiro_offer_rows': len(out),
        'bzzoiro_unique_offer_matches': len(unique_offer_matches),
        'target_date_offer_rows': target_date_rows,
        'target_date_unique_offer_matches': len(target_date_matches),
        'overlap_match_rows': match_overlap_rows,
        'overlap_same_bucket_rows': bucket_overlap_rows,
        'unique_overlap_match_count': len(unique_match_overlap),
        'unique_overlap_same_bucket_match_count': len(unique_bucket_overlap),
        'source_counts': dict(source_counts),
        'odds_reference_aliases': len(idx),
        'no_alias_rows': no_alias_rows,
        'sample_unique_matches': sorted(unique_offer_matches)[:20],
        'sample_overlap_matches': sorted(unique_match_overlap)[:20],
    }
    dump(OUT_OFFERS, {'status': 'ok', 'created_at_utc': now, 'rows': out, 'meta': report})
    dump(OUT_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
