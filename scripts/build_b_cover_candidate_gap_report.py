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

Two defects were fixed in build_candidate_from_bucket.

odds_sources_count was the literal 1.  Inventory rows enriched with SStats prices
carry odds_sources ['odds_api_io', 'sstats'], and that evidence was discarded at
promotion time, so tier A could never see two price sources no matter what the
providers returned.  It is now derived from the offers in the bucket.  account1
and account2 of odds-api.io collapse to one source on purpose: Bet365 and Unibet
arrive through one vendor on one feed, and calling them two independent prices is
how a single-source pick would get an A label.

model_probability was market_probability * (1 + boost), i.e. there was no model.
When real provider xG is available the totals probability now comes from a
Poisson model on those goal rates, blended with the market because no pick has
settled yet, and the sstats_xg hard-context token is claimed only in that case.
"""

import csv
import json
import math
import os
import re
import runpy
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


_NEUTRAL_CONTEXT_SOURCES = {
    'market', 'odds_api_io', 'line_history', 'ensemble', 'market_signal',
    'xg_model_context', 'form_context',
}

# Price-side labels that describe our own pipeline rather than a vendor that
# actually quoted a price. Counting these as odds sources is what makes a
# single-source pick look confirmed.
_PSEUDO_ODDS_SOURCES = {
    'raw_offer_artifacts', 'market', 'market_signal', 'market_implied', 'market_probability',
    'day_inventory', 'dayinventory', 'inventory', 'line_history', 'ensemble', 'self_history',
    'b_cover_market_promotion', 'a_cover_market_promotion', 'controlled_consensus_rescue',
    'controlled_fallback', 'reserve', 'unknown', 'none', 'null',
}

_REAL_ODDS_PROVIDERS = {
    'odds_api_io', 'sstats', 'bzzoiro', 'sportlogic', 'allsportsapi', 'oddsfeed',
    'sportsbook_api', 'sportapi', 'freeapilivefootball',
}


def normalize_provider(value: Any) -> str:
    """Collapse a raw source label to the vendor that served the price.

    odds-api.io account1 and account2 collapse to one name deliberately.  They
    are one vendor on one feed with different bookmaker filters, so Bet365 plus
    Unibet is one price source, not two, and A-tier must not be reachable by
    counting the same feed twice.
    """
    text = norm(value).replace(' ', '_').replace('.', '_')
    if not text:
        return ''
    for prefix, canonical in (
        ('odds_api_io', 'odds_api_io'),
        ('oddsapiio', 'odds_api_io'),
        ('oddsapi_io', 'odds_api_io'),
        ('sstats', 'sstats'),
        ('bzzoiro', 'bzzoiro'),
        ('sportlogic', 'sportlogic'),
        ('allsportsapi', 'allsportsapi'),
        ('oddsfeed', 'oddsfeed'),
    ):
        if text.startswith(prefix):
            return canonical
    return text


def bucket_odds_sources(rows: list[dict[str, Any]]) -> list[str]:
    """Distinct price vendors that actually quoted this bucket.

    The promoted candidate used to declare odds_sources_count = 1 as a literal,
    so an inventory row already enriched to odds_sources ['odds_api_io',
    'sstats'] still arrived at the guards as single-source.  That constant, not
    the providers, is what kept tier_a_odds_sources_below_min:1/2 in the reports.
    """
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ('source', 'provider', 'provider_name', 'feed', 'odds_source', 'origin'):
            name = normalize_provider(row.get(key))
            if not name or name in _PSEUDO_ODDS_SOURCES:
                continue
            if name not in out:
                out.append(name)
            break
    known = [name for name in out if name in _REAL_ODDS_PROVIDERS]
    if known:
        return known
    # A price exists but its vendor is unlabelled. Report one unresolved source
    # instead of inventing a vendor name or silently claiming two.
    return ['unresolved_price_source']


def poisson_under_probability(lambda_total: float, point: float) -> float | None:
    """P(total goals < point) from a Poisson total, half lines only.

    Integer lines push on an exact total and need two-way renormalization; that
    is not worth guessing while no pick has settled.
    """
    try:
        lam = float(lambda_total)
        line = float(point)
    except Exception:
        return None
    if not math.isfinite(lam) or not math.isfinite(line) or lam <= 0 or line <= 0:
        return None
    if abs(abs(line - math.floor(line)) - 0.5) > 1e-9:
        return None
    k_max = int(math.floor(line))
    if k_max > 20 or lam > 12.0:
        return None
    total = 0.0
    for k in range(0, k_max + 1):
        total += math.exp(-lam) * (lam ** k) / math.factorial(k)
    return max(0.0, min(1.0, total))


def _context_hint(row: dict[str, Any]) -> bool:
    """Cheap non-recursive context presence test.

    v5 could recurse context_sources -> context_count -> context_sources on
    rows that had only boolean coverage flags.  In GitHub this made the
    B-cover promotion report disappear completely, so Telegram showed only the
    old diagnostics.  Keep the helper flat and safe.
    """
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        if (
            container.get('context') or container.get('has_context') or container.get('xg')
            or container.get('ready_for_model') or container.get('context_any')
            or container.get('coverage_context')
        ):
            return True
        for key in (
            'context_sources_count', 'context_confirmations_count', 'context_count',
            'contexts_count', 'context_source_count', 'provider_context_count',
        ):
            if count_any(container.get(key)) >= 1:
                return True
    return False


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
        if not key or key in _NEUTRAL_CONTEXT_SOURCES:
            continue
        if key not in seen:
            seen.add(key)
            cleaned.append(key)
    if not cleaned and _context_hint(row):
        cleaned.append('inventory_context')
    return cleaned


def context_count(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    best = 0
    raw_sources: list[str] = []
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in (
            'context_sources', 'context_confirmations', 'all_context_sources',
            'core_context_sources', 'supplemental_context_sources', 'sources',
        ):
            raw_sources.extend(list_from_any(container.get(key)))
            best = max(best, count_any(container.get(key)))
        for key in (
            'context_sources_count', 'context_confirmations_count', 'context_count',
            'contexts_count', 'context_source_count', 'provider_context_count',
        ):
            best = max(best, count_any(container.get(key)))
    cleaned = []
    seen: set[str] = set()
    for src in raw_sources:
        key = norm(src).replace(' ', '_')
        if not key or key in _NEUTRAL_CONTEXT_SOURCES:
            continue
        if key not in seen:
            seen.add(key)
            cleaned.append(key)
    best = max(best, len(cleaned))
    if best <= 0 and _context_hint(row):
        best = 1
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


def _valid_xg_pair(home: float | None, away: float | None) -> tuple[float | None, float | None]:
    if home is None or away is None:
        return None, None
    if home < 0 or away < 0:
        return None, None
    min_team = env_float('PROMOTE_B_COVER_MIN_TEAM_XG_FOR_SANITY', env_float('CONTROLLED_FALLBACK_MIN_TEAM_XG_FOR_SANITY', 0.03))
    min_total = env_float('PROMOTE_B_COVER_MIN_TOTAL_XG_FOR_SANITY', env_float('CONTROLLED_FALLBACK_MIN_TOTAL_XG_FOR_SANITY', 0.25))
    if home <= min_team and away <= min_team:
        return None, None
    if home + away < min_total:
        return None, None
    return home, away


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
            return _valid_xg_pair(home, away)
    return _valid_xg_pair(home, away)


def has_xg(row: dict[str, Any]) -> bool:
    h, a = xg_values(row)
    return h is not None and a is not None


def provider_xg_source(row: dict[str, Any]) -> str:
    """The provider-extraction label for this row's xG, or '' if there is none.

    Only SStats deep enrichment sets sstats_xg_source, and it sets it to
    existing_inventory or missing when its own extraction failed.  Market-implied
    xG is excluded here: it is derived from the price being bet against and it
    arrives as an exact home == away split, which is not evidence about the teams.
    """
    source = norm(row.get('sstats_xg_source')).replace(' ', '_')
    if source in {'', 'existing_inventory', 'missing'}:
        return ''
    if 'market' in source or 'proxy' in source:
        return ''
    home = float_or_none(row.get('sstats_expected_home'))
    away = float_or_none(row.get('sstats_expected_away'))
    if home is None or away is None:
        return ''
    if abs(home - away) <= 1e-6:
        # The market-implied backfill writes an exact 50/50 split.
        return ''
    return source


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
        diagnostics['selected_b_cover_rows'] = 0
        diagnostics['selected_ready_model_rows'] = 0
        return [], diagnostics

    def _candidate_score(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int, int, int]:
        # Prefer row-level coverage truth / repaired inventory over a raw inventory
        # file with the same 229 matches but no per-row books/context fields.
        # The previous v3 patch selected .data/day_inventory first because it had
        # the same row count as coverage-truth, causing promotion considered=0
        # while the Telegram report correctly showed B-cover > 100.
        path, rows = item
        b_cover = 0
        ready_model = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            bc = book_count(row)
            cc = context_count(row)
            if bc >= 1 and cc >= 1:
                b_cover += 1
            if bool(row.get('ready_for_model')) or (bc >= 1 and cc >= 1):
                ready_model += 1
        coverage_bonus = 1 if ('coverage-truth' in path or 'cumulative-coverage' in path or 'coverage' in path) else 0
        return (b_cover, ready_model, len(rows), coverage_bonus)

    selected_path, best = max(candidates, key=_candidate_score)
    selected_b_cover = sum(
        1 for row in best
        if isinstance(row, dict) and book_count(row) >= 1 and context_count(row) >= 1
    )
    selected_ready_model = sum(
        1 for row in best
        if isinstance(row, dict) and (bool(row.get('ready_for_model')) or (book_count(row) >= 1 and context_count(row) >= 1))
    )
    diagnostics['selected_path'] = selected_path
    diagnostics['selected_rows'] = len(best)
    diagnostics['selected_b_cover_rows'] = selected_b_cover
    diagnostics['selected_ready_model_rows'] = selected_ready_model
    diagnostics['candidate_source_scores'] = [
        {
            'path': path,
            'rows': len(rows),
            'b_cover_rows': sum(1 for row in rows if isinstance(row, dict) and book_count(row) >= 1 and context_count(row) >= 1),
            'ready_model_rows': sum(1 for row in rows if isinstance(row, dict) and (bool(row.get('ready_for_model')) or (book_count(row) >= 1 and context_count(row) >= 1))),
        }
        for path, rows in candidates
    ][:12]
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
    odds_sources = bucket_odds_sources(rows)
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

    h_xg, a_xg = xg_values(inv_row)
    require_xg = env_bool('PROMOTE_B_COVER_REQUIRE_XG_FOR_TOTALS', False)
    if require_xg and (h_xg is None or a_xg is None):
        return None, 'promotion_skip_missing_xg'

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
    if h_xg is None or a_xg is None:
        ctx_sources = [src for src in ctx_sources if src != 'model_xg']
    boost_pct += min(1.0, max(1, len(ctx_sources)) * 0.25)
    adjusted = max(0.02, min(0.95, market_prob * (1.0 + boost_pct / 100.0)))

    # A real model, used only when provider xG exists. Until then the number
    # above is a market anchor with a small boost, which is not a model at all:
    # its edge is measured against the same price it is derived from. That is the
    # market_signal segment, and it runs at -11.64% ROI over 39 picks.
    model_mode = 'conservative_median_market_anchor'
    poisson_prob = None
    model_weight = None
    xg_source = provider_xg_source(inv_row)
    if xg_source and h_xg is not None and a_xg is not None:
        under_prob = poisson_under_probability(float(h_xg) + float(a_xg), point)
        if under_prob is not None:
            poisson_prob = under_prob if sel == 'under' else 1.0 - under_prob
            gap_pp = abs(poisson_prob - market_prob) * 100.0
            if gap_pp > env_float('PROMOTE_B_COVER_MAX_MODEL_MARKET_GAP_PP', 15.0):
                # A gap this wide is almost always broken goal rates rather than
                # a real edge the whole market missed.
                return None, 'promotion_skip_model_market_gap_implausible'
            model_weight = min(1.0, max(0.0, env_float('PROMOTE_B_COVER_MODEL_WEIGHT', 0.5)))
            adjusted = max(0.02, min(0.95, poisson_prob * model_weight + market_prob * (1.0 - model_weight)))
            model_mode = 'poisson_provider_xg_blended_with_market'
            # Claim the hard-context token only here: the model actually consumed
            # provider xG for this pick.
            if 'sstats_xg' not in ctx_sources:
                ctx_sources = ctx_sources + ['sstats_xg']

    implied = 1.0 / best_price
    edge_pp = (adjusted - implied) * 100.0
    ev_pct = (adjusted * best_price - 1.0) * 100.0
    if edge_pp < env_float('PROMOTE_B_COVER_MIN_EDGE_PP', 0.35):
        return None, 'promotion_skip_edge_below_min'
    if ev_pct < env_float('PROMOTE_B_COVER_MIN_EV_PCT', 0.7):
        return None, 'promotion_skip_ev_below_min'

    confidence = 58.0
    confidence += min(8.0, len(books) * 1.8)
    confidence += min(6.0, len(ctx_sources) * 1.5)
    confidence += min(6.0, max(0.0, ev_pct) * 0.45)
    confidence += 3.0 if h_xg is not None and a_xg is not None else 0.0
    confidence += 2.0 if poisson_prob is not None else 0.0
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
        'odds_sources': odds_sources,
        'odds_sources_count': len(odds_sources),
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
            f'model={model_mode}',
            f'books={len(books)}',
            f'odds_sources={len(odds_sources)}',
            f'context_sources={len(ctx_sources)}',
            f'best_vs_median={best_vs_median_pct:.2f}%',
        ],
        'source_summary': {
            'selected_source': 'b_cover_market_promotion',
            'selected_bookmaker': selected_book,
            'bookmaker': selected_book,
            'books': books,
            'books_count': len(books),
            'odds_sources': odds_sources,
            'odds_sources_count': len(odds_sources),
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
                'odds_sources': odds_sources,
                'odds_sources_count': len(odds_sources),
            },
            'model': {
                'mode': model_mode,
                'market_probability': round(market_prob, 6),
                'poisson_probability': round(poisson_prob, 6) if poisson_prob is not None else None,
                'blended_probability': round(adjusted, 6),
                'model_weight': model_weight,
                'provider_xg_source': xg_source or None,
                'lambda_home': h_xg,
                'lambda_away': a_xg,
            },
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
    model_modes = Counter(
        str(((cand.get('diagnostics') or {}).get('model') or {}).get('mode') or 'unknown')
        for cand in promoted
    )
    odds_source_histogram = Counter(str(count_any(cand.get('odds_sources'))) for cand in promoted)
    report = {
        'enabled': True,
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'inventory_rows_seen': len(inventory),
        'considered_b_cover_rows': considered,
        'promoted_count': len(promoted),
        'promoted_model_modes': dict(model_modes.most_common()),
        'promoted_odds_sources_histogram': dict(odds_source_histogram.most_common()),
        'reason_counts': dict(reasons.most_common()),
        'offer_diagnostics': offer_diag,
        'sample': promoted[:12],
        'rescue_path': str(RESCUE_PATH),
    }
    write_json(PROMOTION_REPORT_JSON, report)
    return report


def prebuild_coverage_truth_for_promotion() -> dict[str, Any]:
    """Make row-level coverage truth available before promotion/fallback.

    The workflow runs this script before controlled fallback.  Previous versions
    selected the raw day_inventory file because latest-day-inventory-coverage-
    truth.json was created later in the job, so promotion saw inventory_rows=229
    but considered_b_cover_rows=0.  This local prebuild uses only existing run
    artifacts; it does not call external APIs.
    """
    if not env_bool('PROMOTE_B_COVER_PREBUILD_COVERAGE_TRUTH', True):
        return {'enabled': False, 'reason': 'disabled'}
    if os.getenv('BCOVER_PROMOTION_COVERAGE_PREBUILD_RUNNING') == '1':
        return {'enabled': True, 'status': 'skipped_reentrant'}
    os.environ['BCOVER_PROMOTION_COVERAGE_PREBUILD_RUNNING'] = '1'
    started = datetime.now(UTC).isoformat()
    steps: list[dict[str, Any]] = []
    try:
        # Prefer the cumulative script because it merges latest run coverage,
        # repairs source counters, and writes coverage-truth rows.
        for script_name in ('day_inventory_cumulative_coverage.py', 'build_day_inventory_coverage_truth.py'):
            path = ROOT / 'scripts' / script_name
            if not path.exists():
                steps.append({'script': script_name, 'status': 'missing'})
                continue
            try:
                runpy.run_path(str(path), run_name='__main__')
                steps.append({'script': script_name, 'status': 'ok'})
                if script_name == 'day_inventory_cumulative_coverage.py':
                    # This script already calls build_day_inventory_coverage_truth.py.
                    break
            except SystemExit as exc:
                code = getattr(exc, 'code', 0)
                steps.append({'script': script_name, 'status': 'ok' if code in (0, None) else 'error', 'code': code})
                if script_name == 'day_inventory_cumulative_coverage.py' and code in (0, None):
                    break
            except Exception as exc:
                steps.append({'script': script_name, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'})
        return {
            'enabled': True,
            'status': 'ok' if any(step.get('status') == 'ok' for step in steps) else 'no_ok_steps',
            'started_at_utc': started,
            'finished_at_utc': datetime.now(UTC).isoformat(),
            'steps': steps,
        }
    finally:
        os.environ.pop('BCOVER_PROMOTION_COVERAGE_PREBUILD_RUNNING', None)


def main() -> int:
    day = target_date()
    prebuild = prebuild_coverage_truth_for_promotion()
    inventory, inventory_load = load_inventory_with_meta(day)
    if isinstance(inventory_load, dict):
        inventory_load['prebuild_coverage_truth'] = prebuild
    cands_initial = candidate_rows()
    try:
        promotion = promote_candidates(day, inventory, cands_initial)
        if isinstance(promotion, dict):
            promotion['inventory_load'] = inventory_load
            promotion['inventory_rows_seen'] = len(inventory)
            write_json(PROMOTION_REPORT_JSON, promotion)
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
                'provider_xg_source': provider_xg_source(row) or '',
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
        'provider_xg_rows': sum(1 for r in rows_out if r.get('provider_xg_source')),
        'reason_counts': dict(reasons.most_common()),
        'promotion': {**promotion, 'inventory_load': inventory_load} if isinstance(promotion, dict) else promotion,
        'promoted_count': int((promotion or {}).get('promoted_count') or 0) if isinstance(promotion, dict) else 0,
        'sample': rows_out[:40],
    }
    write_json(REPORT_JSON, report)
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['match_key', 'home_team', 'away_team', 'kickoff', 'books_count', 'context_count', 'has_xg_like_context', 'provider_xg_source', 'has_candidate_in_latest_run', 'gap_reason'])
        writer.writeheader()
        writer.writerows(rows_out)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
