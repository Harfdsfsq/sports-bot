from __future__ import annotations

"""Explain and reduce the B-cover -> candidate funnel gap.

This script is intentionally conservative.  It still writes the diagnostic report
introduced in the previous patch, but it also promotes a small shortlist of
B-covered market-consensus totals candidates into latest-rescue-candidates.json.

Why: reports showed B-cover ~90-113 while only 8-12 candidates reached fallback.
The runtime already had raw same-side bookmaker coverage, but the model/fallback
pool did not always get a candidate row for those covered matches.  Promotion is
limited to rows with:
- B-cover: 1+ bookmaker/price and 1+ context;
- a raw totals offer bucket from the same match, matched by id or home/away/date fallback;
- supported public line: integer or .5 only;
- price within the same bookmaker-quorum outlier guard envelope;
- positive conservative canonical EV/edge for promotion diagnostics;
- xG-like context is preferred, but final Telegram publication still enforces xG sanity.

It does not publish picks.  Controlled fallback still applies quality, value,
xG, line movement, price-integrity and duplicate guards.
"""

import csv
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_JSON = EXPORT_DIR / 'latest-b-cover-candidate-gap-report.json'
REPORT_CSV = EXPORT_DIR / 'latest-b-cover-candidate-gap-report.csv'
PROMOTION_REPORT_JSON = EXPORT_DIR / 'latest-b-cover-value-promotion.json'
RESCUE_PATH = EXPORT_DIR / 'latest-rescue-candidates.json'


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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return max(minimum, int(default))
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, int(default))


def env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return float(default)
        return float(str(raw).replace(',', '.'))
    except Exception:
        return float(default)


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я.]+', ' ', text)
    return ' '.join(text.split())


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


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


def key_variants(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ('canonical_match_id', 'match_key', 'event_key', 'id', 'event_id', 'fixture_id', 'game_id'):
        value = str(row.get(key) or '').strip()
        if value:
            out.add('id:' + norm(value))
    h = norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home'))
    a = norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away'))
    d = row_date(row)
    if h and a and d:
        out.add(f'teams:{d}|{h}|{a}')
        out.add(f'teams_rev:{d}|{a}|{h}')
    return {x for x in out if x}


def team_pair(row: dict[str, Any]) -> tuple[str, str]:
    return (
        norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home')),
        norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away')),
    )


def fallback_match_keys(row: dict[str, Any], day: str) -> set[str]:
    keys = set(key_variants(row))
    if keys:
        return keys
    h, a = team_pair(row)
    d = row_date(row) or day
    if h and a and d:
        keys.add(f'teams:{d}|{h}|{a}')
        keys.add(f'teams_rev:{d}|{a}|{h}')
    return keys


def count_any(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        if value in (None, ''):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def context_sources(row: dict[str, Any]) -> list[str]:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    sources: list[str] = []
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in ('context_sources', 'context_confirmations', 'all_context_sources', 'core_context_sources', 'supplemental_context_sources', 'sources'):
            sources.extend(list_from_any(container.get(key)))
    cleaned: list[str] = []
    seen: set[str] = set()
    for src in sources:
        key = norm(src).replace(' ', '_')
        if not key or key in {'market', 'odds_api_io', 'line_history', 'ensemble'}:
            continue
        if key not in seen:
            seen.add(key)
            cleaned.append(key)
    if not cleaned and context_count(row) >= 1:
        cleaned.append('inventory_context')
    return cleaned


def context_count(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    best = 0
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in (
            'context_sources', 'context_confirmations', 'all_context_sources',
            'core_context_sources', 'supplemental_context_sources', 'sources',
            'context_sources_count', 'context_confirmations_count', 'context_count',
            'contexts_count', 'context_source_count', 'provider_context_count',
        ):
            best = max(best, count_any(container.get(key)))
        if (
            container.get('context') or container.get('has_context') or container.get('xg')
            or container.get('ready_for_model') or container.get('context_any')
            or container.get('coverage_context')
        ):
            best = max(best, 1)
    if context_sources(row):
        best = max(best, len(context_sources(row)))
    return best


def book_count(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    best = 0
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in (
            'books_count', 'bookmakers_count', 'bookmaker_count', 'price_confirmations',
            'price_confirmation_sources_count', 'price_sources_count', 'latest_books_max',
            'same_side_books_max', 'same_side_2plus_books', 'bookmaker_coverage_count',
            'bookmakers_or_price_confirmations_count',
        ):
            best = max(best, count_any(container.get(key)))
        for key in ('books', 'bookmakers', 'same_side_books', 'price_sources'):
            best = max(best, count_any(container.get(key)))
        if (
            container.get('odds') or container.get('has_odds') or container.get('odds_any')
            or container.get('coverage_odds') or container.get('ready_for_model')
        ):
            best = max(best, 1)
    return best


def iter_nested(value: Any, depth: int = 0):
    if depth > 6:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from iter_nested(v, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from iter_nested(item, depth + 1)


def float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        f = float(str(value).replace(',', '.'))
        if math.isfinite(f):
            return f
    except Exception:
        return None
    return None


def xg_values(row: dict[str, Any]) -> tuple[float | None, float | None]:
    home_keys = {'expected_home', 'home_expected', 'home_xg', 'xg_home', 'expected_goals_home', 'home_expected_goals'}
    away_keys = {'expected_away', 'away_expected', 'away_xg', 'xg_away', 'expected_goals_away', 'away_expected_goals'}
    home = away = None
    for item in iter_nested(row):
        if not isinstance(item, dict):
            continue
        lower_map = {str(k).lower(): v for k, v in item.items()}
        for key in home_keys:
            if key in lower_map:
                home = float_or_none(lower_map.get(key))
        for key in away_keys:
            if key in lower_map:
                away = float_or_none(lower_map.get(key))
        if home is None and not isinstance(item.get('home'), dict):
            home = float_or_none(item.get('home'))
        if away is None and not isinstance(item.get('away'), dict):
            away = float_or_none(item.get('away'))
        if home is not None and away is not None:
            return home, away
    return home, away


def has_xg(row: dict[str, Any]) -> bool:
    h, a = xg_values(row)
    return h is not None and a is not None


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('matches', 'rows', 'items', 'inventory', 'match_rows', 'coverage_rows'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def load_inventory_with_meta(day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the best available inventory rows without over-filtering by UTC date.

    The daily inventory is frozen by the workflow's local date (Europe/Moscow).
    Some kickoff timestamps are stored in UTC and can fall on the previous UTC day,
    so filtering every row by ``row_date(row) == day`` can accidentally turn a
    healthy 229-row inventory into zero promotion rows.  The file name/path is the
    date contract; row dates are diagnostics only.
    """
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    paths = (
        ROOT / '.data' / 'day_inventory' / f'{day}.json',
        ROOT / '.data' / 'day_inventory' / 'current.json',
        ROOT / '.data' / 'day_inventory' / 'latest.json',
        ROOT / '.data' / 'day_inventory' / 'today.json',
        ROOT / '.data' / 'cache' / 'day_inventory' / f'{day}.json',
        ROOT / '.data' / 'cache' / 'day_inventory' / 'today.json',
        EXPORT_DIR / 'latest-day-inventory-coverage-truth.json',
        EXPORT_DIR / 'latest-day-inventory-cumulative-coverage.json',
        EXPORT_DIR / 'latest-day-inventory-summary.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-day-inventory-coverage-truth.json',
        ROOT / 'artifacts' / 'run-bot' / 'latest-day-inventory-cumulative-coverage.json',
    )
    diagnostics: dict[str, Any] = {'sources': []}
    for path in paths:
        payload = load_json(path, None)
        rows = _rows_from_payload(payload)
        if not rows:
            if path.exists():
                diagnostics['sources'].append({'path': str(path), 'rows': 0, 'status': 'no_rows'})
            continue
        # Prefer target-date rows when that keeps most of the file, but never drop
        # below half of a known inventory because of local/UTC date skew.
        dated = [x for x in rows if isinstance(x, dict) and row_date(x) == day]
        chosen = dated if len(dated) >= max(20, len(rows) // 2) else rows
        candidates.append((str(path), chosen))
        diagnostics['sources'].append({'path': str(path), 'rows': len(rows), 'dated_rows': len(dated), 'chosen_rows': len(chosen)})
    if not candidates:
        diagnostics['selected_path'] = ''
        diagnostics['selected_rows'] = 0
        return [], diagnostics
    selected_path, best = max(candidates, key=lambda item: len(item[1]))
    diagnostics['selected_path'] = selected_path
    diagnostics['selected_rows'] = len(best)
    return best, diagnostics


def load_inventory(day: str) -> list[dict[str, Any]]:
    rows, _ = load_inventory_with_meta(day)
    return rows


def candidate_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in (ROOT / '.logs' / 'debug-last-run.json', EXPORT_DIR / 'latest-rescue-candidates.json', EXPORT_DIR / 'latest-controlled-fallback-report.json'):
        payload = load_json(path, None)
        if isinstance(payload, dict):
            for key in ('candidates_before_quality', 'candidates_after_quality', 'candidates', 'evaluated', 'selected_all'):
                value = payload.get(key)
                if isinstance(value, list):
                    rows.extend([x for x in value if isinstance(x, dict)])
            selected = payload.get('selected')
            if isinstance(selected, dict):
                rows.append(selected)
        elif isinstance(payload, list):
            rows.extend([x for x in payload if isinstance(x, dict)])
    return rows


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
        try:
            p = path.resolve()
        except Exception:
            p = path
        if p not in seen and path.exists():
            seen.add(p)
            out.append(path)
    return out


def as_price(value: Any) -> float | None:
    f = float_or_none(value)
    return f if f is not None and f > 1.0 else None


def bookmaker_of(row: dict[str, Any]) -> str:
    for key in ('bookmaker', 'bookmaker_slug', 'selected_bookmaker', 'selected_bookmaker_slug', 'book', 'sportsbook', 'provider_bookmaker'):
        value = norm(row.get(key))
        if value:
            return value.replace(' ', '_')
    return ''


def family_of(row: dict[str, Any]) -> str:
    raw = norm(row.get('family') or row.get('market_family') or row.get('market') or row.get('market_key') or row.get('type'))
    if any(token in raw for token in ('total', 'totals', 'тотал', 'goals_over_under', 'over_under')):
        return 'totals'
    if any(token in raw for token in ('spread', 'handicap', 'фора')):
        return 'spreads'
    return raw


def selection_key_of(row: dict[str, Any]) -> str:
    raw = norm(row.get('selection_key') or row.get('selection') or row.get('outcome') or row.get('name') or row.get('label'))
    if any(token in raw for token in ('over', 'больше', 'тб')):
        return 'over'
    if any(token in raw for token in ('under', 'меньше', 'тм')):
        return 'under'
    return raw


def point_of(row: dict[str, Any]) -> float | None:
    value = row.get('point') if row.get('point') not in (None, '') else row.get('line') if row.get('line') not in (None, '') else row.get('handicap')
    return float_or_none(value)


def supported_public_total_line(point: float | None) -> bool:
    if point is None or point <= 0:
        return False
    frac = abs(point - math.floor(point))
    return min(abs(frac - 0.0), abs(frac - 0.5), abs(frac - 1.0)) < 1e-9


def offer_like(row: dict[str, Any], day: str | None = None) -> bool:
    has_price = any(as_price(row.get(k)) is not None for k in ('price', 'odds', 'decimal_odds', 'selected_odds'))
    has_book = bool(bookmaker_of(row))
    # Many raw odds leaves do not carry canonical ids, but do carry teams/date.
    # Use the same fallback as bookmaker-backfill; otherwise promotion sees zero buckets.
    has_match = bool(key_variants(row))
    if not has_match and day:
        has_match = bool(fallback_match_keys(row, day))
    return has_price and has_book and has_match


def side_bucket_key(row: dict[str, Any]) -> str | None:
    fam = family_of(row)
    if fam != 'totals':
        return None
    selection = selection_key_of(row)
    if selection not in {'over', 'under'}:
        return None
    point = point_of(row)
    if not supported_public_total_line(point):
        return None
    return f'totals|{selection}|{point:g}'


def collect_offer_buckets(day: str) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    by_match: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    scanned = accepted = 0
    source_counts: Counter[str] = Counter()
    for path in source_paths():
        payload = load_json(path, None)
        if payload is None:
            continue
        accepted_here = 0
        for row in iter_nested(payload):
            if not isinstance(row, dict):
                continue
            scanned += 1
            if not offer_like(row, day):
                continue
            d = row_date(row)
            if d and d != day:
                continue
            bucket_key = side_bucket_key(row)
            if not bucket_key:
                continue
            price = as_price(row.get('price') or row.get('odds') or row.get('decimal_odds') or row.get('selected_odds'))
            book = bookmaker_of(row)
            if price is None or not book:
                continue
            for match_key in fallback_match_keys(row, day):
                bucket = by_match[match_key].setdefault(bucket_key, {'rows': [], 'books': set(), 'prices': []})
                bucket['rows'].append(row)
                bucket['books'].add(book)
                bucket['prices'].append(price)
            accepted += 1
            accepted_here += 1
        if accepted_here:
            source_counts[str(path)] += accepted_here
    # convert sets for diagnostics later where needed
    return by_match, {'scanned_dicts': scanned, 'accepted_offer_rows': accepted, 'source_counts': dict(source_counts)}


def rescue_rows_payload() -> list[dict[str, Any]]:
    payload = load_json(RESCUE_PATH, [])
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ('candidates', 'rows', 'items', 'selected_all'):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([x for x in value if isinstance(x, dict)])
    elif isinstance(payload, list):
        rows.extend([x for x in payload if isinstance(x, dict)])
    return rows


def candidate_signature(row: dict[str, Any]) -> str:
    point = row.get('point')
    try:
        p = f'{float(point):g}' if point not in (None, '') else ''
    except Exception:
        p = norm(point)
    return '|'.join([
        norm(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key')),
        norm(row.get('home_team') or row.get('home')),
        norm(row.get('away_team') or row.get('away')),
        norm(row.get('family') or row.get('market_family')),
        norm(row.get('selection_key') or row.get('selection')),
        p,
    ])


def median(values: list[float]) -> float:
    return float(statistics.median([v for v in values if v > 1.0]))


def build_candidate_from_bucket(inv_row: dict[str, Any], bucket_key: str, bucket: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    rows = [r for r in bucket.get('rows') or [] if isinstance(r, dict)]
    books = sorted(str(x) for x in (bucket.get('books') or set()) if str(x))
    prices = [as_price(r.get('price') or r.get('odds') or r.get('decimal_odds') or r.get('selected_odds')) for r in rows]
    prices = [p for p in prices if p is not None]
    min_books = env_int('PROMOTE_B_COVER_MIN_BOOKS', 1, 1)
    if len(books) < min_books:
        return None, 'promotion_skip_books_below_min'
    if not prices:
        return None, 'promotion_skip_missing_prices'
    try:
        median_price = median(prices)
    except Exception:
        return None, 'promotion_skip_bad_median'
    best_row = max(rows, key=lambda r: as_price(r.get('price') or r.get('odds') or r.get('decimal_odds') or r.get('selected_odds')) or 0.0)
    best_price = as_price(best_row.get('price') or best_row.get('odds') or best_row.get('decimal_odds') or best_row.get('selected_odds'))
    if best_price is None or median_price <= 1.0:
        return None, 'promotion_skip_bad_price'
    if best_price < env_float('CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS', 1.55):
        return None, 'promotion_skip_odds_below_min'
    if best_price > env_float('CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS', 3.05):
        return None, 'promotion_skip_odds_above_max'
    deviation_pct = abs(best_price - median_price) / median_price * 100.0
    if deviation_pct > env_float('PROMOTE_B_COVER_MAX_SELECTED_MEDIAN_DEVIATION_PCT', env_float('CONTROLLED_FALLBACK_TIER_B_MAX_BOOKMAKER_MEDIAN_DEVIATION_PCT', 8.0)):
        return None, 'promotion_skip_price_outlier'

    parts = bucket_key.split('|')
    if len(parts) != 3:
        return None, 'promotion_skip_bad_bucket_key'
    _, sel, point_s = parts
    try:
        point = float(point_s)
    except Exception:
        return None, 'promotion_skip_bad_point'
    selection_ru = 'Больше' if sel == 'over' else 'Меньше'

    # Conservative market-consensus probability: median price is treated as the
    # fair market anchor, then a very small boost is allowed only for non-outlier
    # best-vs-median edge and multi-book/context coverage. Final fallback still
    # recalculates canonical value from this adjusted probability.
    market_prob = max(0.02, min(0.98, 1.0 / median_price))
    best_vs_median_pct = max(0.0, (best_price - median_price) / median_price * 100.0)
    boost_pct = min(3.2, best_vs_median_pct * 0.55)
    # B-tier may have one bookmaker; use context coverage to create candidates for
    # final fallback review, but keep the boost small so weak rows still fail final EV/edge.
    boost_pct += min(0.8, max(0, len(books) - 1) * 0.20)
    ctx_sources = context_sources(inv_row)
    boost_pct += min(1.0, max(1, len(ctx_sources)) * 0.25)
    adjusted = max(0.02, min(0.95, market_prob * (1.0 + boost_pct / 100.0)))
    implied = 1.0 / best_price
    edge_pp = (adjusted - implied) * 100.0
    ev_pct = (adjusted * best_price - 1.0) * 100.0
    if edge_pp < env_float('PROMOTE_B_COVER_MIN_EDGE_PP', 0.35):
        return None, 'promotion_skip_edge_below_min'
    if ev_pct < env_float('PROMOTE_B_COVER_MIN_EV_PCT', 0.7):
        return None, 'promotion_skip_ev_below_min'

    h_xg, a_xg = xg_values(inv_row)
    require_xg = env_bool('PROMOTE_B_COVER_REQUIRE_XG_FOR_TOTALS', False)
    if require_xg and (h_xg is None or a_xg is None):
        return None, 'promotion_skip_missing_xg'

    confidence = 58.0
    confidence += min(8.0, len(books) * 1.8)
    confidence += min(6.0, len(ctx_sources) * 1.5)
    confidence += min(6.0, max(0.0, ev_pct) * 0.45)
    confidence += 3.0 if h_xg is not None and a_xg is not None else 0.0
    confidence = round(max(50.0, min(82.0, confidence)), 3)
    publication_score = round(max(0.0, ev_pct * 1.15 + edge_pp * 2.0 + min(len(books), 4) * 1.2 + min(len(ctx_sources), 3) * 1.1), 3)

    match_key = str(inv_row.get('match_key') or inv_row.get('canonical_match_id') or inv_row.get('event_key') or '').strip()
    if not match_key:
        d = row_date(inv_row) or target_date()
        match_key = f"soccer|{norm(inv_row.get('home_team') or inv_row.get('home')).replace(' ', '_')}|{norm(inv_row.get('away_team') or inv_row.get('away')).replace(' ', '_')}|{d}"
    raw_bucket_offers = []
    for r in rows[:20]:
        raw_bucket_offers.append({
            'bookmaker': bookmaker_of(r),
            'price': as_price(r.get('price') or r.get('odds') or r.get('decimal_odds') or r.get('selected_odds')),
            'family': 'totals',
            'selection': sel,
            'point': point,
            'source': r.get('source') or r.get('provider') or 'raw_offer_artifacts',
        })
    selected_book = bookmaker_of(best_row) or (books[0] if books else '')
    candidate = {
        'match_key': match_key,
        'canonical_match_id': inv_row.get('canonical_match_id') or match_key,
        'home_team': inv_row.get('home_team') or inv_row.get('home'),
        'away_team': inv_row.get('away_team') or inv_row.get('away'),
        'league_name': inv_row.get('league_name') or inv_row.get('league'),
        'commence_time': inv_row.get('commence_time') or inv_row.get('kickoff_utc') or inv_row.get('start_time') or inv_row.get('kickoff'),
        'family': 'totals',
        'market_family': 'totals',
        'selection': selection_ru,
        'selection_key': sel,
        'point': point,
        'odds': round(best_price, 4),
        'selected_odds': round(best_price, 4),
        'bookmaker': selected_book,
        'books_count': len(books),
        'sources_count': max(1, len(ctx_sources)),
        'odds_sources_count': 1,
        'confirmation_sources_count': max(1, len(ctx_sources)),
        'confirmation_sources': ctx_sources,
        'market_probability': round(market_prob, 6),
        'model_probability': round(adjusted, 6),
        'adjusted_probability': round(adjusted, 6),
        'confidence': confidence,
        'publication_score': publication_score,
        'expected_home': h_xg,
        'expected_away': a_xg,
        'ev_pct': round(ev_pct, 3),
        'edge_pct': round(edge_pp, 3),
        'reasons': [
            'mode=b_cover_market_promotion',
            'model=conservative_median_market_anchor',
            f'books={len(books)}',
            f'context_sources={len(ctx_sources)}',
            f'best_vs_median={best_vs_median_pct:.2f}%',
        ],
        'source_summary': {
            'selected_source': 'b_cover_market_promotion',
            'selected_bookmaker': selected_book,
            'bookmaker': selected_book,
            'books': books,
            'books_count': len(books),
            'prices': [round(p, 4) for p in prices[:50]],
            'median_price': round(median_price, 4),
            'selected_vs_median_deviation_pct': round(deviation_pct, 3),
            'context_sources': ctx_sources,
            'raw_bucket_offers': raw_bucket_offers,
        },
        'context': {
            'expected_home': h_xg,
            'expected_away': a_xg,
            'context_sources': ctx_sources,
        },
        'diagnostics': {
            'promotion': {
                'created_by': 'build_b_cover_candidate_gap_report.py',
                'created_at_utc': datetime.now(UTC).isoformat(),
                'median_price': round(median_price, 4),
                'selected_price': round(best_price, 4),
                'selected_vs_median_deviation_pct': round(deviation_pct, 3),
                'canonical_edge_pp': round(edge_pp, 3),
                'canonical_ev_pct': round(ev_pct, 3),
            }
        },
    }
    return candidate, 'promoted'


def promote_candidates(day: str, inventory: list[dict[str, Any]], existing_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not env_bool('PROMOTE_B_COVER_VALUE_CANDIDATES_ENABLED', True):
        return {'enabled': False, 'reason': 'disabled'}
    offer_buckets, offer_diag = collect_offer_buckets(day)
    existing = rescue_rows_payload()
    signatures = {candidate_signature(r) for r in existing_candidates + existing if isinstance(r, dict)}
    promoted: list[dict[str, Any]] = []
    reasons = Counter()
    considered = 0
    limit = env_int('PROMOTE_B_COVER_VALUE_CANDIDATE_LIMIT', 24, 0)
    for row in inventory:
        if not isinstance(row, dict):
            continue
        if book_count(row) < 1 or context_count(row) < 1:
            continue
        considered += 1
        match_buckets: dict[str, dict[str, Any]] = {}
        for key in key_variants(row):
            match_buckets.update(offer_buckets.get(key, {}))
        if not match_buckets:
            reasons['promotion_skip_no_offer_bucket'] += 1
            continue
        candidates_for_match: list[dict[str, Any]] = []
        for bucket_key, bucket in match_buckets.items():
            cand, reason = build_candidate_from_bucket(row, bucket_key, bucket)
            if cand is None:
                reasons[reason] += 1
                continue
            sig = candidate_signature(cand)
            if sig in signatures:
                reasons['promotion_skip_duplicate_candidate'] += 1
                continue
            signatures.add(sig)
            candidates_for_match.append(cand)
        candidates_for_match.sort(key=lambda c: (float(c.get('ev_pct') or 0.0), float(c.get('edge_pct') or 0.0), float(c.get('confidence') or 0.0)), reverse=True)
        for cand in candidates_for_match[:1]:
            promoted.append(cand)
            reasons['promoted'] += 1
            if limit and len(promoted) >= limit:
                break
        if limit and len(promoted) >= limit:
            break
    if promoted:
        merged = promoted + existing
        write_json(RESCUE_PATH, merged[: max(len(merged), limit or len(merged))])
    if considered == 0:
        # Make the failure mode visible in Telegram instead of silently reporting 0/0.
        if not inventory:
            reasons['promotion_zero_inventory_rows'] += 1
        else:
            reasons['promotion_zero_b_cover_rows_after_local_counting'] += 1
    report = {
        'enabled': True,
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'inventory_rows_seen': len(inventory),
        'considered_b_cover_rows': considered,
        'promoted_count': len(promoted),
        'reason_counts': dict(reasons.most_common()),
        'offer_diagnostics': offer_diag,
        'sample': promoted[:12],
        'rescue_path': str(RESCUE_PATH),
    }
    write_json(PROMOTION_REPORT_JSON, report)
    return report


def main() -> int:
    day = target_date()
    inventory, inventory_load = load_inventory_with_meta(day)
    cands_initial = candidate_rows()
    try:
        promotion = promote_candidates(day, inventory, cands_initial)
    except Exception as exc:
        promotion = {
            'enabled': True,
            'status': 'error',
            'error': f'{type(exc).__name__}: {exc}',
            'considered_b_cover_rows': 0,
            'promoted_count': 0,
            'reason_counts': {},
            'inventory_rows_seen': len(inventory),
            'inventory_load': inventory_load,
        }
        write_json(PROMOTION_REPORT_JSON, promotion)
    cands = candidate_rows()
    cand_keys: set[str] = set()
    for row in cands:
        cand_keys.update(key_variants(row))
    rows_out: list[dict[str, Any]] = []
    reasons = Counter()
    b_cover = 0
    for row in inventory:
        b = book_count(row)
        c = context_count(row)
        if b < 1 or c < 1:
            if b < 1:
                reasons['not_b_cover_missing_bookmaker'] += 1
            if c < 1:
                reasons['not_b_cover_missing_context'] += 1
            continue
        b_cover += 1
        keys = key_variants(row)
        has_candidate = bool(keys & cand_keys)
        reason = 'has_candidate' if has_candidate else 'b_cover_no_candidate'
        if not has_candidate:
            if not has_xg(row):
                reason = 'b_cover_no_candidate_missing_xg_like_context'
            reasons[reason] += 1
        else:
            reasons['b_cover_has_candidate'] += 1
        if len(rows_out) < 300:
            rows_out.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'home_team': row.get('home_team') or row.get('home'),
                'away_team': row.get('away_team') or row.get('away'),
                'kickoff': row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time'),
                'books_count': b,
                'context_count': c,
                'has_xg_like_context': has_xg(row),
                'has_candidate_in_latest_run': has_candidate,
                'gap_reason': reason,
            })
    report = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'inventory_rows': len(inventory),
        'inventory_load': inventory_load,
        'candidate_rows_seen': len(cands),
        'candidate_rows_seen_before_promotion': len(cands_initial),
        'b_cover_rows': b_cover,
        'b_cover_without_candidate': sum(1 for r in rows_out if r['gap_reason'].startswith('b_cover_no_candidate')),
        'reason_counts': dict(reasons.most_common()),
        'promotion': {**promotion, 'inventory_load': inventory_load} if isinstance(promotion, dict) else promotion,
        'promoted_count': int((promotion or {}).get('promoted_count') or 0) if isinstance(promotion, dict) else 0,
        'sample': rows_out[:40],
    }
    write_json(REPORT_JSON, report)
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['match_key', 'home_team', 'away_team', 'kickoff', 'books_count', 'context_count', 'has_xg_like_context', 'has_candidate_in_latest_run', 'gap_reason'])
        writer.writeheader()
        writer.writerows(rows_out)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
