from __future__ import annotations

"""Append run candidates to a forward-test ledger.

This script is deliberately read-only with respect to model decisions.  It only
collects published/rejected/watch-only candidates into ``.data/prediction-ledger.jsonl``
so we can later measure CLV/ROI/yield.

v5 fixes:
- line-less sparse value/API rows are merged into the unique same-match market row
  that has a concrete point, so one logical candidate produces one ledger row.

v4 fixes:
- quality/confidence are optional analytics fields for sparse non-fallback rows;
  current-run missing-core counters now require identity + odds + EV + edge, not quality.

v3 fixes:
- value-patch rows often miss ``point`` but contain it in selection text
  ("Меньше 2.5", "Over 2.5").  We extract that line so sparse value/API rows
  merge with rich fallback rows instead of creating duplicate ledger rows.
- fallback rows store most metrics in a nested ``metrics`` object; flatten it.
- current-run missing-metric counters are separated from historical rows.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
LEDGER = ROOT / '.data' / 'prediction-ledger.jsonl'
SUMMARY = EXPORT_DIR / 'latest-prediction-ledger-summary.json'

CORE_METRIC_FIELDS = ('home_team', 'away_team', 'odds', 'ev_pct', 'edge_pp')
OPTIONAL_METRIC_FIELDS = ('quality', 'confidence')


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def extract_point(row: dict[str, Any]) -> Any:
    for key in ('point', 'line', 'total', 'handicap'):
        value = row.get(key)
        if value not in (None, ''):
            return value
    text = ' '.join(str(row.get(k) or '') for k in ('selection', 'selection_key', 'market', 'market_name', 'bet_name'))
    # Supports "Меньше 2.5", "Больше (2.5)", "Under_2_5", "over 3,5".
    m = re.search(r'(?<!\d)(\d{1,2}(?:[.,_]\d{1,2})?)(?!\d)', text)
    if m:
        raw = m.group(1).replace('_', '.').replace(',', '.')
        try:
            return float(raw)
        except Exception:
            return raw
    return ''


def point_token(value: Any) -> str:
    if value in (None, ''):
        return ''
    try:
        num = float(str(value).replace(',', '.'))
        return f'{num:.3f}'.rstrip('0').rstrip('.')
    except Exception:
        return norm(value)


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return row with common aliases and nested metrics lifted to top level."""
    out = dict(row)
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    # Top-level wins, metrics fills holes.
    for key, value in metrics.items():
        out.setdefault(key, value)

    alias_map = {
        'home_team': ('home_team', 'home', 'home_name'),
        'away_team': ('away_team', 'away', 'away_name'),
        'kickoff_utc': ('kickoff_utc', 'commence_time', 'start_time'),
        'family': ('family', 'market_family', 'market'),
        'selection': ('selection', 'selection_key', 'side', 'pick'),
        'odds': ('odds', 'selected_odds', 'best_odds'),
        'ev_pct': ('ev_pct', 'ev', 'canonical_ev_pct', 'canonical_ev'),
        'edge_pp': ('edge_pp', 'edge_pct', 'canonical_edge_pp', 'market_edge_pp'),
        'confidence': ('confidence', 'confidence_score'),
        'quality': ('quality', 'quality_score'),
        'odds_sources_count': ('odds_sources_count', 'independent_odds_sources_count', 'exact_odds_sources_count', 'sources_count'),
        'context_sources_count': ('context_sources_count', 'confirmation_sources_count'),
        'books_count': ('books_count', 'exact_books_count'),
    }
    for target, keys in alias_map.items():
        if out.get(target) not in (None, '', [], {}):
            continue
        for key in keys:
            if row.get(key) not in (None, '', [], {}):
                out[target] = row.get(key)
                break
            if metrics.get(key) not in (None, '', [], {}):
                out[target] = metrics.get(key)
                break

    if out.get('point') in (None, ''):
        out['point'] = extract_point(out)
    return out


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [flatten_row(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in (
        'candidates', 'rows', 'data', 'evaluated', 'blocked_top', 'near_miss',
        'selected', 'selected_all', 'published_candidates', 'sample',
        'input_sample', 'output_sample', 'rows_sample', 'candidate_sample',
        'rejected_samples',
    ):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(flatten_row(x) for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(flatten_row(val))
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(rows(decision))
    return out


def identity(row: dict[str, Any]) -> str:
    row = flatten_row(row)
    base = row.get('match_key') or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}"
    return '|'.join([
        norm(base),
        norm(row.get('family') or row.get('market_family') or row.get('market')),
        norm(row.get('selection') or row.get('selection_key')),
        point_token(extract_point(row)),
    ])


def identity_base(row: dict[str, Any]) -> str:
    """Line-agnostic identity used only to merge sparse audit rows.

    Some runtime artifacts (especially candidate-value audit rows) omit ``point``
    while fallback/API rows export the same candidate with a concrete line.  A
    sparse no-point row must not become a second ledger/calibration candidate.
    """
    row = flatten_row(row)
    base = row.get('match_key') or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}"
    return '|'.join([
        norm(base),
        norm(row.get('family') or row.get('market_family') or row.get('market')),
        norm(row.get('selection') or row.get('selection_key')),
    ])


def _key_point(key_value: str) -> str:
    parts = str(key_value or '').split('|')
    return parts[-1] if len(parts) >= 4 else ''


def _key_base(key_value: str) -> str:
    parts = str(key_value or '').split('|')
    return '|'.join(parts[:3]) if len(parts) >= 4 else str(key_value or '')


def collapse_missing_line_rows(
    merged: dict[str, dict[str, Any]],
    stage_seen: dict[str, set[str]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]] | None, int]:
    """Merge line-less sparse rows into the unique line-bearing sibling.

    This is intentionally conservative: a no-line row is merged only when there is
    exactly one non-empty-point candidate with the same match/family/selection
    base.  Ambiguous cases are left untouched for audit visibility.
    """
    by_base: dict[str, list[str]] = defaultdict(list)
    for key_value in merged:
        if _key_point(key_value):
            by_base[_key_base(key_value)].append(key_value)

    remap: dict[str, str] = {}
    for key_value in list(merged):
        if _key_point(key_value):
            continue
        targets = by_base.get(_key_base(key_value), [])
        if len(targets) == 1:
            remap[key_value] = targets[0]

    collapsed = 0
    for source_key, target_key in remap.items():
        if source_key not in merged or target_key not in merged:
            continue
        merged[target_key] = merge_row(merged[target_key], merged[source_key])
        del merged[source_key]
        if stage_seen is not None:
            stage_seen[target_key].update(stage_seen.get(source_key, set()))
            stage_seen.pop(source_key, None)
        collapsed += 1
    return merged, stage_seen, collapsed


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in ('reasons', 'reject_reasons', 'reject_reasons_ru', 'quality_reasons'):
        val = row.get(name)
        if isinstance(val, list):
            out.extend(str(x) for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            out.extend(x.strip() for x in re.split(r'[,|;/]+', val) if x.strip())
    # preserve order, drop duplicates
    seen = set()
    result = []
    for item in out:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def existing_ids() -> set[str]:
    ids: set[str] = set()
    if not LEDGER.exists():
        return ids
    for line in LEDGER.read_text(encoding='utf-8').splitlines():
        try:
            obj = json.loads(line)
            if obj.get('ledger_id'):
                ids.add(str(obj['ledger_id']))
        except Exception:
            continue
    return ids


def row_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Prefer rich fallback/publication rows over sparse audit rows."""
    row = flatten_row(row)
    return (
        int(row.get('home_team') not in (None, '')) + int(row.get('away_team') not in (None, '')),
        int(row.get('odds') not in (None, '')),
        int(row.get('ev_pct') not in (None, '')),
        int(row.get('edge_pp') not in (None, '')),
        int(row.get('quality') not in (None, '')),
    )


def merge_row(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    base = flatten_row(base)
    incoming = flatten_row(incoming)
    # If incoming is richer, start from it then fill with previous.
    if row_rank(incoming) > row_rank(base):
        merged = dict(incoming)
        for k, v in base.items():
            if merged.get(k) in (None, '', [], {}):
                merged[k] = v
    else:
        merged = dict(base)
        for k, v in incoming.items():
            if merged.get(k) in (None, '', [], {}) and v not in (None, '', [], {}):
                merged[k] = v
    return flatten_row(merged)


def collect_current_rows() -> list[dict[str, Any]]:
    sources = [
        ('before_quality', EXPORT_DIR / 'latest-candidates-before-quality.json'),
        ('after_quality', EXPORT_DIR / 'latest-candidates-after-quality.json'),
        ('value_patch', EXPORT_DIR / 'latest-candidate-value-runtime-patch.json'),
        ('api_coverage', EXPORT_DIR / 'latest-api-coverage-consensus-runtime-patch.json'),
        ('quality_relief', EXPORT_DIR / 'latest-quality-consensus-safe-relief.json'),
        ('fallback', EXPORT_DIR / 'latest-controlled-fallback-report.json'),
        ('normalized_publication', EXPORT_DIR / 'latest-normalized-publication-payloads.json'),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)

    for stage, path in sources:
        for row in rows(load_json(path, None)):
            k = identity(row)
            merged[k] = merge_row(merged.get(k, {}), row)
            stage_seen[k].add(stage)

    merged, stage_seen, line_less_collapsed = collapse_missing_line_rows(merged, stage_seen)

    out = []
    run_id = os.getenv('GITHUB_RUN_ID') or datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    for k, row in merged.items():
        row = flatten_row(row)
        r = reasons(row)
        status = 'published' if row.get('telegram_sent') is True or row.get('published') is True else 'rejected_or_watch'
        if any('watch' in x.lower() or 'watch only' in x.lower() for x in r):
            status = 'watch_only'
        out.append({
            'ledger_id': f'{run_id}|{k}',
            'run_id': run_id,
            'created_at_utc': datetime.now(UTC).isoformat(),
            'candidate_key': k,
            'status': status,
            'stage_seen': sorted(stage_seen[k]),
            'match_key': row.get('match_key'),
            'home_team': row.get('home_team'),
            'away_team': row.get('away_team'),
            'league_name': row.get('league_name'),
            'kickoff_utc': row.get('kickoff_utc') or row.get('commence_time'),
            'family': row.get('family'),
            'selection': row.get('selection'),
            'point': extract_point(row),
            'odds': as_float(row.get('odds')),
            'ev_pct': as_float(row.get('ev_pct')),
            'edge_pp': as_float(row.get('edge_pp')),
            'confidence': as_float(row.get('confidence')),
            'quality': as_float(row.get('quality')),
            'odds_sources_count': as_int(row.get('odds_sources_count')),
            'context_sources_count': as_int(row.get('context_sources_count')),
            'books_count': as_int(row.get('books_count')),
            'reasons': r,
            'settlement': row.get('settlement') if isinstance(row.get('settlement'), dict) else {},
        })
    return out


def _missing_core(row: dict[str, Any]) -> bool:
    return any(row.get(k) in (None, '') for k in CORE_METRIC_FIELDS)


def _read_ledger_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not LEDGER.exists():
        return out
    for line in LEDGER.read_text(encoding='utf-8').splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            pass
    return out


def summarize(current_run_id: str = '') -> dict[str, Any]:
    rows_existing = _read_ledger_rows()
    by_status = Counter(str(r.get('status') or 'unknown') for r in rows_existing)
    by_reason = Counter()
    for row in rows_existing:
        for reason in row.get('reasons') or []:
            by_reason[str(reason)] += 1
    current = [r for r in rows_existing if str(r.get('run_id') or '') == str(current_run_id)] if current_run_id else []
    return {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'ledger_path': str(LEDGER),
        'current_run_id': current_run_id,
        'total_rows': len(rows_existing),
        'current_run_rows': len(current),
        'rows_missing_core_metrics_total': sum(1 for r in rows_existing if _missing_core(r)),
        'rows_missing_core_metrics_current_run': sum(1 for r in current if _missing_core(r)),
        'by_status': dict(by_status),
        'top_reasons': by_reason.most_common(30),
        'notes': [
            'Use this ledger for forward testing before trusting ROI.',
            'Published/rejected/watch-only rows are accumulated; settlement fields can be filled by a future results job.',
            'rows_missing_core_metrics_current_run is the main quality signal; total may include older rows produced before ledger fixes.',
            'quality/confidence are optional when a row never reached fallback; they should not make a valid EV/edge row look broken.',
        ],
    }


def main() -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    run_id = os.getenv('GITHUB_RUN_ID') or datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    seen = existing_ids()
    new_rows = [r for r in collect_current_rows() if r['ledger_id'] not in seen]
    if new_rows:
        with LEDGER.open('a', encoding='utf-8') as f:
            for row in new_rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    summary = summarize(run_id)
    summary['new_rows_added'] = len(new_rows)
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
