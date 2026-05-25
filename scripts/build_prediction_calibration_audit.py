from __future__ import annotations

"""Build a before/after calibration audit for every candidate row we can see.

v5 merges line-less sparse value/API rows into the unique matching line-bearing row across artifacts.

v4 treats quality as optional for sparse non-fallback rows while preserving it when exported.

v3 merges sparse value/API rows with rich fallback/quality rows by extracting the
line from selection text when ``point`` is missing.  This keeps one row per
logical candidate and preserves home/away/odds/quality metrics.
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
OUT = EXPORT_DIR / 'latest-prediction-calibration-audit.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def norm(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def extract_point(row: dict[str, Any]) -> Any:
    for key_name in ('point', 'line', 'total', 'handicap'):
        value = row.get(key_name)
        if value not in (None, ''):
            return value
    text = ' '.join(str(row.get(k) or '') for k in ('selection', 'selection_key', 'market', 'market_name', 'bet_name'))
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
    out = dict(row)
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    for key_name, value in metrics.items():
        out.setdefault(key_name, value)
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
        for k in keys:
            if row.get(k) not in (None, '', [], {}):
                out[target] = row.get(k)
                break
            if metrics.get(k) not in (None, '', [], {}):
                out[target] = metrics.get(k)
                break
    if out.get('point') in (None, ''):
        out['point'] = extract_point(out)
    return out


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [flatten_row(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for name in (
        'candidates', 'rows', 'data', 'evaluated', 'blocked_top', 'near_miss',
        'sample', 'selected', 'selected_all', 'input_sample', 'output_sample',
        'rows_sample', 'candidate_sample', 'rejected_samples',
    ):
        val = payload.get(name)
        if isinstance(val, list):
            out.extend(flatten_row(x) for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(flatten_row(val))
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def key(row: dict[str, Any]) -> str:
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
    seen = set()
    result = []
    for item in out:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def collect(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidate_rows(load_json(path, None)):
        row = flatten_row(row)
        row['_artifact'] = path.name
        k = key(row)
        current = out.get(k, {})
        out[k] = merge_row(current, row)
    return out


def richness(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    row = flatten_row(row)
    return (
        int(row.get('home_team') not in (None, '')) + int(row.get('away_team') not in (None, '')),
        int(row.get('odds') not in (None, '')),
        int(row.get('ev_pct') not in (None, '')),
        int(row.get('edge_pp') not in (None, '')),
        int(row.get('quality') not in (None, '')),
    )


def merge_row(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a = flatten_row(a) if a else {}
    b = flatten_row(b) if b else {}
    if richness(b) > richness(a):
        merged = dict(b)
        for k, v in a.items():
            if merged.get(k) in (None, '', [], {}):
                merged[k] = v
    else:
        merged = dict(a)
        for k, v in b.items():
            if merged.get(k) in (None, '', [], {}) and v not in (None, '', [], {}):
                merged[k] = v
    return flatten_row(merged)


def prefer_after(*rows: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for row in rows:
        merged = merge_row(merged, row)
    return merged


def main() -> int:
    before = collect(EXPORT_DIR / 'latest-candidates-before-quality.json')
    after = collect(EXPORT_DIR / 'latest-candidates-after-quality.json')
    value_patch = collect(EXPORT_DIR / 'latest-candidate-value-runtime-patch.json')
    fallback = collect(EXPORT_DIR / 'latest-controlled-fallback-report.json')
    quality = collect(EXPORT_DIR / 'latest-quality-consensus-safe-relief.json')
    api = collect(EXPORT_DIR / 'latest-api-coverage-consensus-runtime-patch.json')
    # Collapse sparse no-point rows into the unique matching line-bearing row in every artifact map
    # before we build all_keys.  This prevents value-only rows with key "...||" from
    # appearing as a duplicate next to fallback rows with key "...||2.5/3.5".
    before, _, before_collapsed = collapse_missing_line_rows(before)
    after, _, after_collapsed = collapse_missing_line_rows(after)
    value_patch, _, value_collapsed = collapse_missing_line_rows(value_patch)
    fallback, _, fallback_collapsed = collapse_missing_line_rows(fallback)
    quality, _, quality_collapsed = collapse_missing_line_rows(quality)
    api, _, api_collapsed = collapse_missing_line_rows(api)

    # Cross-artifact collapse: use any line-bearing row from any artifact as the
    # canonical target for sparse rows from another artifact.
    union: dict[str, dict[str, Any]] = {}
    for mapping in (before, after, value_patch, fallback, quality, api):
        for key_value, row in mapping.items():
            union[key_value] = merge_row(union.get(key_value, {}), row)
    union, _, cross_collapsed = collapse_missing_line_rows(union)

    def canonicalize(mapping: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key_value, row in mapping.items():
            target = key_value
            if not _key_point(key_value):
                candidates = [k for k in union if _key_base(k) == _key_base(key_value) and _key_point(k)]
                if len(candidates) == 1:
                    target = candidates[0]
            out[target] = merge_row(out.get(target, {}), row)
        return out

    before = canonicalize(before)
    after = canonicalize(after)
    value_patch = canonicalize(value_patch)
    fallback = canonicalize(fallback)
    quality = canonicalize(quality)
    api = canonicalize(api)

    all_keys = sorted(set(before) | set(after) | set(value_patch) | set(fallback) | set(quality) | set(api))
    rows_out: list[dict[str, Any]] = []
    reason_counts = Counter()
    negative_after = 0
    missing_core = 0
    for k in all_keys:
        b = prefer_after(before.get(k, {}), value_patch.get(k, {}), api.get(k, {}))
        a = prefer_after(api.get(k, {}), quality.get(k, {}), after.get(k, {}), fallback.get(k, {}))
        # Fallback usually has canonical post-quality/final metrics and rich identity fields.
        rich = prefer_after(b, a, fallback.get(k, {}), quality.get(k, {}), api.get(k, {}))
        ev_before = as_float(b.get('ev_pct'))
        ev_after = as_float(a.get('ev_pct'))
        edge_before = as_float(b.get('edge_pp'))
        edge_after = as_float(a.get('edge_pp'))
        r = reasons(a) or reasons(rich) or reasons(b)
        for item in r:
            reason_counts[item] += 1
        negative_after += int(ev_after is not None and ev_after < 0)
        row_out = {
            'key': k,
            'match_key': rich.get('match_key'),
            'home_team': rich.get('home_team'),
            'away_team': rich.get('away_team'),
            'family': rich.get('family'),
            'selection': rich.get('selection'),
            'point': extract_point(rich),
            'odds': as_float(rich.get('odds')),
            'ev_before_pct': ev_before,
            'ev_after_pct': ev_after,
            'edge_before_pp': edge_before,
            'edge_after_pp': edge_after,
            'ev_delta_pct': round(ev_after - ev_before, 4) if ev_before is not None and ev_after is not None else None,
            'edge_delta_pp': round(edge_after - edge_before, 4) if edge_before is not None and edge_after is not None else None,
            'quality': as_float(rich.get('quality')),
            'confidence': as_float(rich.get('confidence')),
            'books_count': rich.get('books_count'),
            'odds_sources_count': rich.get('odds_sources_count'),
            'context_sources_count': rich.get('context_sources_count'),
            'reasons': r,
            'stage_seen': {
                'before_quality': k in before,
                'after_quality': k in after,
                'value_patch': k in value_patch,
                'fallback': k in fallback,
                'quality_relief': k in quality,
                'api_coverage': k in api,
            },
        }
        if any(row_out.get(field) in (None, '') for field in ('home_team', 'away_team', 'odds')):
            missing_core += 1
        rows_out.append(row_out)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID') or '',
        'counts': {
            'candidate_keys': len(all_keys),
            'before_quality': len(before),
            'after_quality': len(after),
            'value_patch': len(value_patch),
            'line_less_rows_collapsed': before_collapsed + after_collapsed + value_collapsed + fallback_collapsed + quality_collapsed + api_collapsed + cross_collapsed,
            'api_coverage': len(api),
            'fallback': len(fallback),
            'quality_relief': len(quality),
            'negative_ev_after_calibration': negative_after,
            'rows_missing_core_metrics': missing_core,
            'rows_with_quality': sum(1 for row in rows_out if row.get('quality') is not None),
        },
        'top_reasons': reason_counts.most_common(20),
        'rows': rows_out[:250],
        'notes': [
            'This is an audit only. It does not change candidate probabilities, EV, or publication guards.',
            'Rows merge discovery, API coverage, quality and fallback artifacts so metrics are not lost when one layer exports sparse data.',
        ],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
