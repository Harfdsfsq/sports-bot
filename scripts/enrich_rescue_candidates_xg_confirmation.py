from __future__ import annotations

"""Best-effort xG/confirmation enrichment for rescue candidates.

The first source is always real context already produced in the current run.  If
providers matched the market but did not return xG for a near-kickoff totals
candidate, use the same-side market probability as a neutral Poisson sanity
fallback.  That fallback does not create value and does not bypass price,
movement, duplicate or bookmaker guards; it only prevents good 2+ book B-tier
candidates from dying as `missing_total_xg_sanity` when the market itself implies
a reasonable total-goals anchor.
"""

import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
OUT = EXPORT_DIR / 'latest-rescue-xg-confirmation-enrichment.json'

CANDIDATE_PATHS = [
    EXPORT_DIR / 'latest-rescue-candidates.json',
    EXPORT_DIR / 'latest-picks.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-rescue-candidates.json',
]
CONTEXT_PATHS = [
    EXPORT_DIR / 'latest-context-observations.json',
    EXPORT_DIR / 'latest-context-observation-rows.json',
    EXPORT_DIR / 'latest-match-contexts.json',
    EXPORT_DIR / 'latest-match-serving.json',
    EXPORT_DIR / 'latest-matches.json',
    EXPORT_DIR / 'latest-model-debug.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-context-observations.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-match-contexts.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-matches.json',
]


def load_json(path: Path, default: Any) -> Any:
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


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('rows', 'matches', 'items', 'data', 'candidates', 'rescue_candidates', 'forecast_rows', 'contexts', 'observations'):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
    if out:
        return out
    for value in payload.values():
        if isinstance(value, dict):
            out.append(value)
        elif isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
    return out


def fnum(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        value = float(str(value).replace(',', '.'))
        return value if math.isfinite(value) else None
    except Exception:
        return None


def env_float(name: str, default: float) -> float:
    value = fnum(os.getenv(name))
    return default if value is None else float(value)


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def key_of(row: dict[str, Any]) -> str:
    explicit = norm(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key') or row.get('fixture_id'))
    if explicit:
        return explicit
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name'))
    date = str(row.get('commence_time') or row.get('kickoff') or row.get('start_time') or row.get('date') or '')[:10]
    return '|'.join(x for x in (home, away, date) if x)


def pair_key(row: dict[str, Any]) -> str:
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name'))
    return f'{home}|{away}' if home and away else ''


def nested_find(row: Any, keys: set[str]) -> Any:
    stack: list[Any] = [row]
    seen: set[int] = set()
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
        lower = {str(k).replace('-', '_').replace(' ', '_').lower(): v for k, v in cur.items()}
        for key in keys:
            value = lower.get(key)
            if value not in (None, ''):
                return value
        for key in ('source_summary', 'diagnostics', 'context', 'contexts', 'provider_context', 'features', 'metrics', 'xg', 'model_xg', 'expected_goals', 'prediction', 'raw_context', 'payload', 'details'):
            value = lower.get(key)
            if isinstance(value, (dict, list)):
                stack.append(value)
    return None


def xg_from_context(row: dict[str, Any]) -> dict[str, Any]:
    home = nested_find(row, {'expected_home', 'home_expected', 'home_xg', 'xg_home', 'expected_goals_home', 'home_expected_goals', 'homexg'})
    away = nested_find(row, {'expected_away', 'away_expected', 'away_xg', 'xg_away', 'expected_goals_away', 'away_expected_goals', 'awayxg'})
    total = nested_find(row, {'total_xg', 'xg_total', 'expected_total', 'expected_goals_total', 'total_expected_goals', 'expectedgoals'})
    home_f = fnum(home)
    away_f = fnum(away)
    total_f = fnum(total)
    if home_f is not None and away_f is not None and home_f + away_f > 0.25:
        return {'expected_home': round(home_f, 4), 'expected_away': round(away_f, 4), 'xg_source': 'context_home_away'}
    if total_f is not None and total_f > 0.25:
        return {'total_xg': round(total_f, 4), 'expected_home': round(total_f / 2.0, 4), 'expected_away': round(total_f / 2.0, 4), 'xg_source': 'context_total_split'}
    return {}


def poisson_cdf(k: int, lam: float) -> float:
    if k < 0 or lam < 0:
        return 0.0
    term = math.exp(-lam)
    total = term
    for i in range(1, int(k) + 1):
        term *= lam / i
        total += term
    return max(0.0, min(1.0, total))


def total_prob(selection: str, line: float, lam: float) -> float:
    side = norm(selection)
    is_over = 'over' in side or 'больше' in side or side in {'tb', 'тб'}
    is_under = 'under' in side or 'меньше' in side or side in {'tm', 'тм'}
    if not is_over and not is_under:
        return 0.0
    frac = round(line - math.floor(line), 2)
    def over_prob(single_line: float) -> float:
        if abs(single_line - round(single_line)) < 1e-9:
            return 1.0 - poisson_cdf(int(round(single_line)), lam)
        return 1.0 - poisson_cdf(int(math.floor(single_line)), lam)
    def under_prob(single_line: float) -> float:
        if abs(single_line - round(single_line)) < 1e-9:
            return poisson_cdf(int(round(single_line)) - 1, lam)
        return poisson_cdf(int(math.floor(single_line)), lam)
    if frac in {0.25, 0.75}:
        low = math.floor(line) if frac == 0.25 else math.floor(line) + 0.5
        high = math.floor(line) + 0.5 if frac == 0.25 else math.floor(line) + 1.0
        return ((over_prob(low) + over_prob(high)) / 2.0) if is_over else ((under_prob(low) + under_prob(high)) / 2.0)
    return over_prob(line) if is_over else under_prob(line)


def infer_lambda(selection: str, line: float, probability: float) -> float | None:
    if not (0.03 < probability < 0.97) or not (0.25 <= line <= 7.5):
        return None
    lo, hi = 0.05, 8.0
    side = norm(selection)
    is_over = 'over' in side or 'больше' in side or side in {'tb', 'тб'}
    is_under = 'under' in side or 'меньше' in side or side in {'tm', 'тм'}
    if not is_over and not is_under:
        return None
    for _ in range(64):
        mid = (lo + hi) / 2.0
        p = total_prob(selection, line, mid)
        if is_over:
            if p < probability:
                lo = mid
            else:
                hi = mid
        else:
            if p > probability:
                lo = mid
            else:
                hi = mid
    return (lo + hi) / 2.0


def market_implied_xg(row: dict[str, Any]) -> dict[str, Any]:
    if not env_bool('RESCUE_MARKET_IMPLIED_XG_ENABLED', True):
        return {}
    family = norm(row.get('family') or row.get('market_family'))
    if family not in {'totals', 'teamtotals'}:
        return {}
    books = int(fnum(row.get('books_count')) or fnum((row.get('source_summary') or {}).get('books_count')) or 0) if isinstance(row.get('source_summary') or {}, dict) else int(fnum(row.get('books_count')) or 0)
    min_books = int(env_float('RESCUE_MARKET_IMPLIED_XG_MIN_BOOKS', 2.0))
    if books < min_books:
        return {}
    line = fnum(row.get('point') or row.get('line') or row.get('handicap'))
    probability = fnum(row.get('market_probability'))
    selection = str(row.get('selection_key') or row.get('selection') or '')
    if line is None or probability is None or not selection:
        return {}
    lam = infer_lambda(selection, float(line), float(probability))
    if lam is None:
        return {}
    home_prob = fnum(row.get('home_win_probability') or nested_find(row, {'home_win_probability', 'home_probability', 'prob_home_win'}))
    away_prob = fnum(row.get('away_win_probability') or nested_find(row, {'away_win_probability', 'away_probability', 'prob_away_win'}))
    share = 0.5
    if home_prob is not None and away_prob is not None and home_prob + away_prob > 0:
        share = max(0.28, min(0.72, home_prob / (home_prob + away_prob)))
    return {
        'total_xg': round(lam, 4),
        'expected_home': round(lam * share, 4),
        'expected_away': round(lam * (1.0 - share), 4),
        'xg_source': 'market_implied_total_xg',
        'market_probability': round(float(probability), 6),
        'line': round(float(line), 3),
        'selection': selection,
        'books_count': books,
    }


def provider_set(row: dict[str, Any]) -> set[str]:
    providers: set[str] = set()
    for key in ('provider', 'source', 'selected_source'):
        value = row.get(key)
        if norm(value):
            providers.add(norm(value))
    for key in ('providers', 'sources', 'context_sources', 'confirmation_sources'):
        value = row.get(key)
        if isinstance(value, list):
            providers.update(norm(v) for v in value if norm(v))
        elif isinstance(value, str) and value.strip():
            providers.update(norm(x) for x in re.split(r'[,;+|]', value) if norm(x))
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('provider', 'source', 'selected_source'):
        if norm(ss.get(key)):
            providers.add(norm(ss.get(key)))
    for key in ('providers', 'sources', 'context_sources', 'confirmation_sources'):
        value = ss.get(key)
        if isinstance(value, list):
            providers.update(norm(v) for v in value if norm(v))
        elif isinstance(value, str) and value.strip():
            providers.update(norm(x) for x in re.split(r'[,;+|]', value) if norm(x))
    return {p for p in providers if p}


def collect_context_index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in CONTEXT_PATHS:
        for row in rows_from_payload(load_json(path, {})):
            row = dict(row)
            row['_context_path'] = str(path)
            k = key_of(row)
            if k:
                by_key[k].append(row)
            p = pair_key(row)
            if p:
                by_pair[p].append(row)
    return by_key, by_pair


def candidate_files() -> list[tuple[Path, list[dict[str, Any]], Any, str | None]]:
    out = []
    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        rows = rows_from_payload(payload)
        if rows:
            container_key = None
            if isinstance(payload, dict):
                for key in ('rows', 'candidates', 'rescue_candidates', 'items', 'data'):
                    if isinstance(payload.get(key), list):
                        container_key = key
                        break
            out.append((path, rows, payload, container_key))
    return out


def save_candidate_payload(path: Path, payload: Any, rows: list[dict[str, Any]], container_key: str | None) -> None:
    if isinstance(payload, dict) and container_key:
        payload[container_key] = rows
        write_json(path, payload)
    elif isinstance(payload, list):
        write_json(path, rows)


def install_xg(row: dict[str, Any], xg_payload: dict[str, Any], source_path: str, diag_source: str) -> None:
    ss = row.setdefault('source_summary', {}) if isinstance(row.setdefault('source_summary', {}), dict) else {}
    if not isinstance(ss, dict):
        ss = {}
        row['source_summary'] = ss
    diag = row.setdefault('diagnostics', {}) if isinstance(row.setdefault('diagnostics', {}), dict) else {}
    if not isinstance(diag, dict):
        diag = {}
        row['diagnostics'] = diag
    row['expected_home'] = xg_payload.get('expected_home')
    row['expected_away'] = xg_payload.get('expected_away')
    ss['xg'] = {
        'home': xg_payload.get('expected_home'),
        'away': xg_payload.get('expected_away'),
        'total_xg': xg_payload.get('total_xg') or round(float(xg_payload.get('expected_home') or 0) + float(xg_payload.get('expected_away') or 0), 4),
        'source': xg_payload.get('xg_source'),
        'context_path': source_path,
        'market_probability': xg_payload.get('market_probability'),
        'line': xg_payload.get('line'),
    }
    ss['model_xg'] = ss['xg']
    diag['xg_enrichment'] = {'added': True, 'source_mode': diag_source, **ss['xg']}


def main() -> int:
    by_key, by_pair = collect_context_index()
    files = candidate_files()
    candidates_seen = 0
    xg_added = 0
    market_implied_xg_added = 0
    confirmation_added = 0
    missing_context_match = 0
    touched_files: list[str] = []
    examples: list[dict[str, Any]] = []

    for path, rows, payload, container_key in files:
        changed = False
        for row in rows:
            candidates_seen += 1
            matches = by_key.get(key_of(row), []) or by_pair.get(pair_key(row), [])
            providers: set[str] = set()
            xg_payload: dict[str, Any] = {}
            xg_path = ''
            if matches:
                for ctx in matches:
                    providers.update(provider_set(ctx))
                    if not xg_payload:
                        xg_payload = xg_from_context(ctx)
                        xg_path = str(ctx.get('_context_path') or '') if xg_payload else ''
            else:
                missing_context_match += 1
            ss = row.setdefault('source_summary', {}) if isinstance(row.setdefault('source_summary', {}), dict) else {}
            if not isinstance(ss, dict):
                ss = {}
                row['source_summary'] = ss
            existing_providers = set()
            for key in ('confirmation_sources', 'context_sources', 'sources'):
                value = ss.get(key) or row.get(key)
                if isinstance(value, list):
                    existing_providers.update(norm(v) for v in value if norm(v))
                elif isinstance(value, str):
                    existing_providers.update(norm(x) for x in re.split(r'[,;+|]', value) if norm(x))
            merged_providers = sorted((providers | existing_providers) - {''})
            if providers and len(merged_providers) > len(existing_providers):
                ss['confirmation_sources'] = merged_providers
                ss['context_sources'] = merged_providers
                ss['confirmation_sources_count'] = len(merged_providers)
                ss['context_sources_count'] = len(merged_providers)
                confirmation_added += 1
                changed = True
            if not xg_payload:
                xg_payload = market_implied_xg(row)
                xg_path = 'market_probability_from_candidate'
                if xg_payload:
                    market_implied_xg_added += 1
            if xg_payload and not xg_from_context(row):
                install_xg(row, xg_payload, xg_path, str(xg_payload.get('xg_source') or 'context'))
                xg_added += 1
                changed = True
                if len(examples) < 8:
                    examples.append({
                        'match': row.get('match_name') or f"{row.get('home_team') or row.get('home')} — {row.get('away_team') or row.get('away')}",
                        'expected_home': row.get('expected_home'),
                        'expected_away': row.get('expected_away'),
                        'source': xg_payload.get('xg_source'),
                        'context_path': xg_path,
                    })
        if changed:
            save_candidate_payload(path, payload, rows, container_key)
            touched_files.append(str(path))

    report = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'candidates_seen': candidates_seen,
        'context_keys': len(by_key),
        'context_pairs': len(by_pair),
        'xg_added': xg_added,
        'market_implied_xg_added': market_implied_xg_added,
        'confirmation_added': confirmation_added,
        'missing_context_match': missing_context_match,
        'touched_files': touched_files,
        'examples': examples,
        'note': 'Uses real context first; market-implied xG is neutral totals sanity only and does not bypass value/price/movement guards.',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
