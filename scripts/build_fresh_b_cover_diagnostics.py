from __future__ import annotations

"""Build fresh B-cover diagnostics from current runtime artifacts.

This is diagnostic-only. It does not publish picks and does not relax guards. The
important fix is that Bzzoiro overlap offers are now treated as current offer
rows, so the report no longer says B-cover has no current offer when Bzzoiro has
fresh bridged prices.
"""

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-fresh-b-cover-diagnostics.json'


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


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('rows', 'matches', 'items', 'data', 'candidates', 'evaluated_candidates', 'rescue_candidates', 'offers', 'snapshots', 'lines', 'selected_all'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def as_int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set)):
            return len({norm(x) for x in value if norm(x)})
        if isinstance(value, dict):
            return len(value)
        return int(float(value or 0))
    except Exception:
        return 0


def as_float(value: Any) -> float:
    try:
        f = float(str(value or 0).replace(',', '.'))
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def date_of(row: dict[str, Any]) -> str:
    for key in ('date', 'kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'match_key', 'canonical_match_id'):
        m = re.search(r'20\d{2}-\d{2}-\d{2}', str(row.get(key) or ''))
        if m:
            return m.group(0)
    return ''


def parse_key_parts(raw_key: Any) -> tuple[str, str, str]:
    raw = str(raw_key or '').strip()
    parts = [p.strip() for p in raw.split('|') if p.strip()]
    date = ''
    for part in parts:
        m = re.search(r'20\d{2}-\d{2}-\d{2}', part)
        if m:
            date = m.group(0)
            break
    text_parts = [p for p in parts if not re.search(r'20\d{2}-\d{2}-\d{2}', p) and norm(p) not in {'soccer', 'football', 'teams'}]
    home = text_parts[0] if len(text_parts) >= 2 else ''
    away = text_parts[1] if len(text_parts) >= 2 else ''
    return date, home, away


def team_pair(row: dict[str, Any]) -> tuple[str, str]:
    home = row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home')
    away = row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away')
    if home and away:
        return str(home), str(away)
    _, kh, ka = parse_key_parts(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key'))
    return kh, ka


def aliases(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    raw = str(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key') or '').strip()
    if raw:
        out.update({raw, norm(raw)})
        kd, kh, ka = parse_key_parts(raw)
    else:
        kd, kh, ka = '', '', ''
    date = date_of(row) or kd
    home, away = team_pair(row)
    home = home or kh
    away = away or ka
    hn, an = norm(home), norm(away)
    if date and hn and an:
        out.update({
            f'{date}|{hn}|{an}',
            f'teams:{date}|{hn}|{an}',
            f'soccer|{hn}|{an}|{date}',
            f'{date}|{hn[:18]}|{an[:18]}',
        })
    return {x for x in out if x and x.strip('|')}


def nested_count(row: dict[str, Any], *names: str) -> int:
    best = 0
    for src in (row, row.get('metadata'), row.get('source_summary'), row.get('coverage')):
        if not isinstance(src, dict):
            continue
        for name in names:
            best = max(best, as_int(src.get(name)))
    return best


def book_count(row: dict[str, Any]) -> int:
    best = nested_count(row, 'books_count', 'bookmakers_count', 'price_confirmation_sources_count', 'same_side_books_max')
    best = max(best, nested_count(row, 'books', 'bookmakers', 'price_sources', 'price_confirmations'))
    if best <= 0 and (row.get('bookmaker') or row.get('book') or row.get('odds') or row.get('price')):
        best = 1
    return best


def context_count(row: dict[str, Any]) -> int:
    best = nested_count(row, 'context_sources_count', 'confirmation_sources_count', 'context_count')
    best = max(best, nested_count(row, 'context_sources', 'context_confirmations', 'confirmation_sources', 'providers'))
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    if best <= 0 and (row.get('context') or row.get('has_context') or cov.get('context')):
        best = 1
    return best


def source_count(row: dict[str, Any]) -> int:
    best = nested_count(row, 'sources_count', 'odds_sources_count', 'independent_odds_sources_count')
    best = max(best, nested_count(row, 'sources', 'odds_sources', 'independent_odds_sources'))
    return best or int(bool(row.get('source') or row.get('provider') or row.get('bookmaker')))


def kickoff_utc(row: dict[str, Any]) -> datetime | None:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff'):
        raw = row.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        if re.fullmatch(r'20\d{2}-\d{2}-\d{2}', text):
            continue
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
        except Exception:
            continue
    return None


def is_active(row: dict[str, Any], now: datetime) -> bool:
    kickoff = kickoff_utc(row)
    return True if kickoff is None else kickoff >= now - timedelta(minutes=10)


def family(row: dict[str, Any]) -> str:
    raw = norm(row.get('family') or row.get('market_family') or row.get('market') or row.get('market_key') or row.get('sport_key'))
    if any(x in raw for x in ('total', 'over under', 'goals')):
        return 'totals'
    if any(x in raw for x in ('spread', 'handicap', 'фора')):
        return 'spreads'
    return raw


def selection(row: dict[str, Any]) -> str:
    raw = norm(row.get('selection_key') or row.get('selection') or row.get('outcome') or row.get('name'))
    market_name = str(row.get('market_name') or '').lower()
    if '.over@' in market_name or any(x in raw for x in ('over', 'больше', 'тб')):
        return 'over'
    if '.under@' in market_name or any(x in raw for x in ('under', 'меньше', 'тм')):
        return 'under'
    return raw


def point(value: Any) -> str:
    if value in (None, '', 'null'):
        return ''
    try:
        f = float(str(value).replace(',', '.'))
        return str(int(f)) if f.is_integer() else f'{f:g}'
    except Exception:
        return norm(value)


def offer_bucket(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return ('/'.join(sorted(aliases(row)))[:160], family(row), selection(row), point(row.get('point') or row.get('line') or row.get('handicap') or row.get('total')))


def ensure_bzzoiro_bridge() -> None:
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main
        bridge_main()
    except Exception:
        pass


def collect_offers() -> tuple[list[dict[str, Any]], dict[str, int]]:
    ensure_bzzoiro_bridge()
    paths = [
        EXPORT / 'latest-odds-api-io-offer-snapshot.json',
        EXPORT / 'latest-line-snapshots.json',
        EXPORT / 'latest-consensus-lines.json',
        EXPORT / 'latest-bzzoiro-overlap-offers.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-line-snapshots.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-bzzoiro-overlap-offers.json',
    ]
    out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for path in paths:
        accepted = 0
        for row in rows(load(path, {})):
            row = dict(row)
            if not aliases(row):
                continue
            sig = json.dumps([sorted(aliases(row))[:3], row.get('source') or row.get('provider'), row.get('bookmaker') or row.get('book'), family(row), selection(row), row.get('point') or row.get('line'), row.get('price') or row.get('odds')], ensure_ascii=False, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            row['_source_path'] = str(path)
            out.append(row)
            accepted += 1
        if accepted:
            counts[str(path)] = accepted
    return out, dict(counts)


def inventory_rows() -> tuple[Path, list[dict[str, Any]]]:
    candidates = [
        EXPORT / 'latest-day-inventory-coverage-truth.json',
        ROOT / '.data' / 'day_inventory' / 'latest.json',
        ROOT / '.data' / 'day_inventory' / 'current.json',
        ROOT / '.data' / 'cache' / 'day_inventory' / f'{datetime.now(timezone.utc).date().isoformat()}.json',
    ]
    for path in candidates:
        rs = rows(load(path, {}))
        if rs:
            return path, rs
    return candidates[0], []


def collect_fallback_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('evaluated_candidates', 'candidates', 'rows', 'reserve_candidates', 'items', 'selected_all'):
        value = report.get(key) if isinstance(report, dict) else None
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
    return out


def reason_counts(report: dict[str, Any], fallback_rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for key in ('reject_reasons', 'reason_counts', 'reasons'):
        value = report.get(key) if isinstance(report, dict) else None
        if isinstance(value, dict):
            for reason, count in value.items():
                counter[str(reason)] += as_int(count) or 1
    for row in fallback_rows:
        for key in ('reasons', 'reject_reasons', 'hard_reject_reasons', 'failure_reasons'):
            value = row.get(key)
            if isinstance(value, list):
                counter.update(str(x) for x in value if str(x).strip())
            elif isinstance(value, str) and value.strip():
                counter[value] += 1
    return dict(counter)


def has_xg(row: dict[str, Any]) -> bool:
    text = json.dumps(row, ensure_ascii=False).lower()
    return any(k in text for k in ('expected_home', 'expected_away', 'home_xg', 'away_xg', 'total_xg', 'model_xg'))


def main() -> int:
    now = datetime.now(timezone.utc)
    inv_path, inv = inventory_rows()
    b_rows = [row for row in inv if book_count(row) >= 1 and context_count(row) >= 1]
    active_b_rows = [row for row in b_rows if is_active(row, now)]
    offers, offer_source_counts = collect_offers()

    offer_aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for offer in offers:
        for alias in aliases(offer):
            offer_aliases[alias].append(offer)
        bucket_rows[offer_bucket(offer)].append(offer)

    def with_offer(items: list[dict[str, Any]]) -> int:
        return sum(1 for row in items if any(alias in offer_aliases for alias in aliases(row)))

    total_buckets = 0
    bzz_buckets = 0
    bucket_book_hist: Counter[str] = Counter()
    for bucket, bucket_items in bucket_rows.items():
        fam = bucket[1]
        if fam not in {'totals', 'spreads', 'teamtotals'}:
            continue
        books = {norm(x.get('bookmaker') or x.get('book')) for x in bucket_items if norm(x.get('bookmaker') or x.get('book'))}
        total_buckets += 1
        bucket_book_hist[str(len(books))] += 1
        bzz_buckets += int(any(norm(x.get('source') or x.get('provider')) == 'bzzoiro' for x in bucket_items))

    promotion = load(EXPORT / 'latest-b-cover-value-promotion.json', {})
    report = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    fb_rows = collect_fallback_rows(report if isinstance(report, dict) else {})
    payload = {
        'created_at_utc': now.isoformat(),
        'inventory_path': str(inv_path),
        'inventory_rows': len(inv),
        'b_cover_rows': len(b_rows),
        'active_b_cover_rows': len(active_b_rows),
        'offer_rows_seen': len(offers),
        'offer_source_counts': offer_source_counts,
        'b_cover_with_any_current_offer_match': with_offer(b_rows),
        'b_cover_without_current_offer_match': max(0, len(b_rows) - with_offer(b_rows)),
        'active_b_cover_with_any_current_offer_match': with_offer(active_b_rows),
        'active_b_cover_without_current_offer_match': max(0, len(active_b_rows) - with_offer(active_b_rows)),
        'current_market_buckets_totals_spreads': total_buckets,
        'current_market_bzzoiro_buckets_totals_spreads': bzz_buckets,
        'current_market_bucket_book_count_histogram': dict(bucket_book_hist),
        'bzzoiro_overlap_bridge': load(EXPORT / 'latest-bzzoiro-overlap-bridge.json', {}),
        'promotion_reason_counts': promotion.get('reason_counts') if isinstance(promotion, dict) else {},
        'promotion_promoted_count': promotion.get('promoted_count') if isinstance(promotion, dict) else None,
        'promotion_considered_b_cover_rows': promotion.get('considered_b_cover_rows') if isinstance(promotion, dict) else None,
        'fallback_candidates_seen': report.get('candidates_seen') if isinstance(report, dict) else len(fb_rows),
        'fallback_status': report.get('status') if isinstance(report, dict) else None,
        'fallback_rows_seen': len(fb_rows),
        'fallback_reason_counts': reason_counts(report if isinstance(report, dict) else {}, fb_rows),
        'fallback_single_source_candidates': sum(1 for row in fb_rows if source_count(row) < 2),
        'fallback_missing_xg_candidates': sum(1 for row in fb_rows if not has_xg(row)),
        'fallback_low_edge_candidates': sum(1 for row in fb_rows if as_float(row.get('canonical_edge_pp') or row.get('edge_pp') or row.get('edge')) < 5.0),
        'fallback_low_ev_candidates': sum(1 for row in fb_rows if as_float(row.get('canonical_ev_pct') or row.get('ev_pct') or row.get('ev')) < 8.0),
        'fallback_low_confidence_candidates': sum(1 for row in fb_rows if as_float(row.get('confidence') or row.get('quality') or row.get('quality_score')) < 70.0),
    }
    dump(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
