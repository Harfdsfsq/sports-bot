from __future__ import annotations

"""Build before/after calibration audit for real prediction candidates.

v8 notes:
- api_coverage-only rows are reported separately as opportunity-only evidence and
  excluded from forecast-calibration rows.
- sparse line-less rows are merged into the unique same-match/family/selection row
  with a concrete line.
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
PREDICTION_STAGES = {'before_quality','after_quality','value_patch','quality_relief','fallback','normalized_publication'}


def load_json(path: Path, default: Any = None) -> Any:
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


def first_nonempty(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    for key in keys:
        value = row.get(key)
        if value not in (None, '', [], {}):
            return value
        value = metrics.get(key)
        if value not in (None, '', [], {}):
            return value
    return None


def extract_point(row: dict[str, Any]) -> Any:
    value = first_nonempty(row, 'point', 'line', 'total', 'handicap')
    if value not in (None, ''):
        return value
    text = ' '.join(str(first_nonempty(row, k) or '') for k in ('selection','selection_key','market','market_name','bet_name'))
    m = re.search(r'(?<!\d)(\d{1,2}(?:[.,_]\d{1,2})?)(?!\d)', text)
    if not m:
        return ''
    raw = m.group(1).replace('_','.').replace(',', '.')
    try:
        return float(raw)
    except Exception:
        return raw


def point_token(value: Any) -> str:
    if value in (None, ''):
        return ''
    try:
        return f"{float(str(value).replace(',', '.')):.3f}".rstrip('0').rstrip('.')
    except Exception:
        return norm(value)


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row or {})
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    out = dict(metrics)
    out.update({k: v for k, v in row.items() if k != 'metrics'})
    aliases = {
        'home_team': ('home_team','home','home_name'),
        'away_team': ('away_team','away','away_name'),
        'kickoff_utc': ('kickoff_utc','commence_time','start_time'),
        'family': ('family','market_family','market'),
        'selection': ('selection','selection_key','side','pick'),
        'odds': ('odds','selected_odds','best_odds'),
        'ev_pct': ('ev_pct','ev','canonical_ev_pct','canonical_ev'),
        'edge_pp': ('edge_pp','edge_pct','canonical_edge_pp','market_edge_pp'),
        'confidence': ('confidence','confidence_score'),
        'quality': ('quality','quality_score'),
        'odds_sources_count': ('odds_sources_count','independent_odds_sources_count','exact_odds_sources_count','sources_count'),
        'context_sources_count': ('context_sources_count','confirmation_sources_count'),
        'books_count': ('books_count','exact_books_count'),
    }
    for target, keys in aliases.items():
        if out.get(target) not in (None, '', [], {}):
            continue
        value = first_nonempty(row, *keys)
        if value not in (None, '', [], {}):
            out[target] = value
    if out.get('point') in (None, ''):
        out['point'] = extract_point(out)
    return out


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [flatten_row(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('candidates','rows','data','evaluated','blocked_top','near_miss','sample','selected','selected_all','input_sample','output_sample','rows_sample','candidate_sample','rejected_samples'):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(flatten_row(x) for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(flatten_row(val))
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def identity(row: dict[str, Any]) -> str:
    row = flatten_row(row)
    base = row.get('match_key') or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc')}"
    return '|'.join([norm(base), norm(row.get('family')), norm(row.get('selection')), point_token(extract_point(row))])


def key_base(key_value: str) -> str:
    parts = str(key_value or '').split('|')
    return '|'.join(parts[:3]) if len(parts) >= 4 else str(key_value or '')


def key_point(key_value: str) -> str:
    parts = str(key_value or '').split('|')
    return parts[-1] if len(parts) >= 4 else ''


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in ('reasons','reject_reasons','reject_reasons_ru','quality_reasons'):
        value = row.get(name)
        if isinstance(value, list):
            out.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            out.extend(x.strip() for x in re.split(r'[,|;/]+', value) if x.strip())
    seen=set(); result=[]
    for item in out:
        if item not in seen:
            seen.add(item); result.append(item)
    return result


def rank(row: dict[str, Any]) -> tuple[int,int,int,int,int,int]:
    row = flatten_row(row)
    return (
        int(row.get('home_team') not in (None,'')) + int(row.get('away_team') not in (None,'')),
        int(row.get('odds') not in (None,'')),
        int(row.get('ev_pct') not in (None,'')),
        int(row.get('edge_pp') not in (None,'')),
        int(row.get('quality') not in (None,'')),
        int(bool(reasons(row))),
    )


def merge_row(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a = flatten_row(a) if a else {}
    b = flatten_row(b) if b else {}
    if rank(b) > rank(a):
        merged = dict(b); fill = a
    else:
        merged = dict(a); fill = b
    for k,v in fill.items():
        if merged.get(k) in (None,'',[],{}) and v not in (None,'',[],{}):
            merged[k]=v
    for before_key, metric_key in (('ev_before_pct', 'ev_pct'), ('edge_before_pp', 'edge_pp')):
        if merged.get(before_key) in (None, '', [], {}) and fill.get(metric_key) not in (None, '', [], {}):
            merged[before_key] = fill.get(metric_key)
    return flatten_row(merged)


def collect(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidate_rows(load_json(path, None)):
        k = identity(row)
        out[k] = merge_row(out.get(k, {}), row)
    return out


def collapse(merged: dict[str, dict[str, Any]], stage_seen: dict[str, set[str]]) -> tuple[int, int]:
    by_base: dict[str, list[str]] = defaultdict(list)
    for k in merged:
        if key_point(k):
            by_base[key_base(k)].append(k)
    collapsed = ambiguous = 0
    for src in list(merged):
        if key_point(src):
            continue
        targets = by_base.get(key_base(src), [])
        if len(targets) == 1:
            tgt = targets[0]
            merged[tgt] = merge_row(merged[tgt], merged[src])
            stage_seen[tgt].update(stage_seen.get(src, set()))
            merged.pop(src, None); stage_seen.pop(src, None)
            collapsed += 1
        elif len(targets) > 1:
            ambiguous += 1
    return collapsed, ambiguous


def main() -> int:
    artifacts = [
        ('before_quality', EXPORT_DIR/'latest-candidates-before-quality.json'),
        ('after_quality', EXPORT_DIR/'latest-candidates-after-quality.json'),
        ('value_patch', EXPORT_DIR/'latest-candidate-value-runtime-patch.json'),
        ('api_coverage', EXPORT_DIR/'latest-api-coverage-consensus-runtime-patch.json'),
        ('quality_relief', EXPORT_DIR/'latest-quality-consensus-safe-relief.json'),
        ('fallback', EXPORT_DIR/'latest-controlled-fallback-report.json'),
        ('normalized_publication', EXPORT_DIR/'latest-normalized-publication-payloads.json'),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)
    artifact_counts = {}
    for stage, path in artifacts:
        rows = candidate_rows(load_json(path, None))
        artifact_counts[stage] = len(rows)
        for row in rows:
            k = identity(row)
            merged[k] = merge_row(merged.get(k, {}), row)
            stage_seen[k].add(stage)
    collapsed, ambiguous = collapse(merged, stage_seen)

    rows_out=[]; opportunity_only=[]; reason_counts=Counter(); missing_core=0; negative_after=0
    for k,row in sorted(merged.items()):
        stages = set(stage_seen[k])
        row = flatten_row(row)
        r = reasons(row)
        if not (stages & PREDICTION_STAGES):
            opportunity_only.append({'key': k, 'home_team': row.get('home_team'), 'away_team': row.get('away_team'), 'odds': as_float(row.get('odds')), 'stage_seen': sorted(stages)})
            continue
        for item in r:
            reason_counts[item]+=1
        ev_after = as_float(row.get('ev_pct'))
        if ev_after is not None and ev_after < 0:
            negative_after += 1
        out = {
            'key': k,
            'match_key': row.get('match_key'),
            'home_team': row.get('home_team'),
            'away_team': row.get('away_team'),
            'family': row.get('family'),
            'selection': row.get('selection'),
            'point': extract_point(row),
            'odds': as_float(row.get('odds')),
            'ev_before_pct': as_float(row.get('ev_before_pct') or row.get('raw_ev_pct')),
            'ev_after_pct': ev_after,
            'edge_before_pp': as_float(row.get('edge_before_pp') or row.get('raw_edge_pp')),
            'edge_after_pp': as_float(row.get('edge_pp')),
            'quality': as_float(row.get('quality')),
            'confidence': as_float(row.get('confidence')),
            'books_count': row.get('books_count'),
            'odds_sources_count': row.get('odds_sources_count'),
            'context_sources_count': row.get('context_sources_count'),
            'reasons': r,
            'stage_seen': {stage: stage in stages for stage, _ in artifacts},
        }
        if any(out.get(f) in (None,'') for f in ('home_team','away_team','odds')):
            missing_core += 1
        rows_out.append(out)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID') or '',
        'counts': {
            'candidate_keys': len(rows_out),
            **artifact_counts,
            'coverage_only_rows_excluded': len(opportunity_only),
            'line_less_rows_collapsed': collapsed,
            'line_less_rows_ambiguous': ambiguous,
            'negative_ev_after_calibration': negative_after,
            'rows_missing_core_metrics': missing_core,
            'rows_with_quality': sum(1 for r in rows_out if r.get('quality') is not None),
        },
        'top_reasons': reason_counts.most_common(20),
        'rows': rows_out[:250],
        'opportunity_only_rows_sample': opportunity_only[:50],
        'notes': ['Audit only; does not change model decisions or publication guards.', 'api_coverage-only rows are kept as opportunity-only evidence, not prediction rows.'],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    main()
