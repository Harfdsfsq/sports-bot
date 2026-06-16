from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-fresh-b-cover-diagnostics.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
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
    for key in ('rows', 'matches', 'items', 'data', 'candidates', 'evaluated_candidates', 'rescue_candidates'):
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


def as_float(value: Any) -> float:
    try:
        if value in (None, ''):
            return 0.0
        f = float(str(value).replace(',', '.'))
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def nested_get(row: dict[str, Any], *keys: str) -> Any:
    if not isinstance(row, dict):
        return None
    stack: list[Any] = [row]
    seen: set[int] = set()
    wanted = {k.replace('-', '_').replace(' ', '_').lower() for k in keys}
    while stack:
        cur = stack.pop(0)
        mid = id(cur)
        if mid in seen:
            continue
        seen.add(mid)
        if isinstance(cur, list):
            stack.extend(x for x in cur if isinstance(x, (dict, list)))
            continue
        if not isinstance(cur, dict):
            continue
        lowered = {str(k).replace('-', '_').replace(' ', '_').lower(): v for k, v in cur.items()}
        for key in wanted:
            v = lowered.get(key)
            if v not in (None, ''):
                return v
        for key in ('source_summary', 'diagnostics', 'context', 'contexts', 'provider_context', 'features', 'metrics', 'xg', 'model_xg', 'expected_goals', 'prediction', 'raw_context', 'payload'):
            v = lowered.get(key)
            if isinstance(v, (dict, list)):
                stack.append(v)
    return None


def books_count(row: dict[str, Any]) -> int:
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('books_count', 'bookmakers_count', 'price_confirmations', 'priced_books_count'):
        n = as_int(row.get(key)) or as_int(ss.get(key))
        if n:
            return n
    for key in ('books', 'bookmakers', 'priced_books'):
        n = as_int(row.get(key) or ss.get(key))
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


def source_count(row: dict[str, Any]) -> int:
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('sources_count', 'odds_sources_count', 'independent_odds_sources_count', 'confirmation_sources_count'):
        n = as_int(row.get(key)) or as_int(ss.get(key))
        if n:
            return n
    for key in ('sources', 'odds_sources', 'independent_odds_sources', 'confirmation_sources'):
        n = as_int(row.get(key) or ss.get(key))
        if n:
            return n
    return 1 if (row.get('source') or ss.get('source') or row.get('bookmaker')) else 0


def match_key(row: dict[str, Any]) -> str:
    explicit = norm(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key'))
    if explicit:
        return explicit
    home = norm(row.get('home_team') or row.get('home'))
    away = norm(row.get('away_team') or row.get('away'))
    date = str(row.get('date') or row.get('commence_time') or row.get('kickoff') or row.get('start_time') or '')[:10]
    return '|'.join(x for x in (home, away, date) if x)


def offer_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    family = norm(row.get('family') or row.get('market_family') or row.get('market'))
    if family in {'total', 'totals goals', 'over under', 'over under 25', 'over under 35'}:
        family = 'totals'
    selection = norm(row.get('selection_key') or row.get('selection') or row.get('name') or row.get('outcome'))
    if any(x in selection for x in ('under', 'меньше', 'тм')):
        selection = 'under'
    elif any(x in selection for x in ('over', 'больше', 'тб')):
        selection = 'over'
    return (match_key(row), family, selection, point(row.get('point') or row.get('line') or row.get('handicap') or row.get('total')))


def collect_offer_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths = [
        ROOT / '.data' / 'exports' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / '.data' / 'exports' / 'latest-line-snapshots.json',
        ROOT / '.data' / 'exports' / 'latest-consensus-lines.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-line-snapshots.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-consensus-lines.json',
        ROOT / 'artifacts' / 'run-bot' / 'exports' / 'latest-odds-api-io-offer-snapshot.json',
        ROOT / 'artifacts' / 'run-bot' / 'exports' / 'latest-line-snapshots.json',
    ]
    seen: set[str] = set()
    for path in paths:
        payload = load_json(path, None)
        for row in rows_from_payload(payload):
            row = dict(row)
            row['_source_path'] = str(path)
            mk = match_key(row)
            if not mk:
                continue
            sig = json.dumps([mk, row.get('bookmaker') or row.get('book'), row.get('market') or row.get('family'), row.get('selection') or row.get('outcome'), row.get('point') or row.get('line')], ensure_ascii=False, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            rows.append(row)
    return rows


def collect_fallback_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ('evaluated_candidates', 'candidates', 'rows', 'reserve_candidates', 'items'):
        val = report.get(key) if isinstance(report, dict) else None
        if isinstance(val, list):
            rows.extend(x for x in val if isinstance(x, dict))
    # Some reports keep the useful rows under debug/pools.
    pools = report.get('pools') if isinstance(report, dict) else None
    if isinstance(pools, dict):
        for val in pools.values():
            if isinstance(val, list):
                rows.extend(x for x in val if isinstance(x, dict))
    # Dedupe by publication/match key.
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get('dedupe_key') or row.get('fingerprint') or offer_key(row))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def reason_counter(report: dict[str, Any], rows: list[dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for src_key in ('reject_reasons', 'reason_counts', 'reasons'):
        val = report.get(src_key) if isinstance(report, dict) else None
        if isinstance(val, dict):
            for key, count in val.items():
                counter[str(key)] += as_int(count) or 1
    for row in rows:
        for key in ('reasons', 'reject_reasons', 'hard_reject_reasons', 'failure_reasons'):
            val = row.get(key)
            if isinstance(val, list):
                for reason in val:
                    if str(reason).strip():
                        counter[str(reason).strip()] += 1
            elif isinstance(val, str) and val.strip():
                counter[val.strip()] += 1
        reason = row.get('reason') or row.get('reject_reason')
        if reason:
            counter[str(reason)] += 1
    return counter


def row_has_xg(row: dict[str, Any]) -> bool:
    home = nested_get(row, 'expected_home', 'home_xg', 'xg_home', 'home_expected_goals')
    away = nested_get(row, 'expected_away', 'away_xg', 'xg_away', 'away_expected_goals')
    total = nested_get(row, 'total_xg', 'xg_total', 'expected_total', 'expected_goals_total')
    return (as_float(home) > 0 and as_float(away) > 0) or as_float(total) > 0


def main() -> int:
    inv_paths = [
        ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-truth.json',
        ROOT / '.data' / 'cache' / 'day_inventory' / f'{datetime.now(timezone.utc).date().isoformat()}.json',
        ROOT / '.data' / 'day_inventory' / 'latest.json',
        ROOT / '.data' / 'day_inventory' / 'current.json',
    ]
    inv_path = inv_paths[0]
    inv_rows: list[dict[str, Any]] = []
    for path in inv_paths:
        inv_rows = rows_from_payload(load_json(path, {}))
        if inv_rows:
            inv_path = path
            break

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
    for row in b_rows:
        if match_key(row) in offers_by_match:
            has_match += 1
        else:
            no_match += 1

    current_total_buckets = 0
    bucket_book_counts: Counter = Counter()
    for key, bucket in offers_by_bucket.items():
        if key[1] in {'totals', 'spreads', 'teamtotals'}:
            books = {norm(x.get('bookmaker') or x.get('book')) for x in bucket if norm(x.get('bookmaker') or x.get('book'))}
            current_total_buckets += 1
            bucket_book_counts[str(len(books))] += 1

    promotion = load_json(ROOT / '.data' / 'exports' / 'latest-b-cover-value-promotion.json', {})
    report = load_json(ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json', {})
    fallback_rows = collect_fallback_rows(report)
    fallback_reasons = reason_counter(report, fallback_rows)

    single_source = sum(1 for row in fallback_rows if source_count(row) < 2)
    missing_xg = sum(1 for row in fallback_rows if not row_has_xg(row))
    low_edge = sum(1 for row in fallback_rows if as_float(row.get('canonical_edge_pp') or row.get('edge_pp') or row.get('edge')) < 5.0)
    low_ev = sum(1 for row in fallback_rows if as_float(row.get('canonical_ev_pct') or row.get('ev_pct') or row.get('ev')) < 8.0)
    low_conf = sum(1 for row in fallback_rows if as_float(row.get('confidence') or row.get('quality') or row.get('quality_score')) < 70.0)

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
        'promotion_considered_b_cover_rows': promotion.get('considered_b_cover_rows') if isinstance(promotion, dict) else None,
        'fallback_candidates_seen': report.get('candidates_seen') if isinstance(report, dict) else len(fallback_rows),
        'fallback_status': report.get('status') if isinstance(report, dict) else None,
        'fallback_rows_seen': len(fallback_rows),
        'fallback_reason_counts': dict(fallback_reasons),
        'fallback_single_source_candidates': single_source,
        'fallback_missing_xg_candidates': missing_xg,
        'fallback_low_edge_candidates': low_edge,
        'fallback_low_ev_candidates': low_ev,
        'fallback_low_confidence_candidates': low_conf,
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
