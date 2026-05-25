from __future__ import annotations

"""Build a before/after calibration audit for every candidate row we can see."""

import json
import os
import re
from collections import Counter
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


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('candidates', 'rows', 'data', 'evaluated', 'blocked_top', 'near_miss', 'sample', 'selected', 'selected_all'):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(val)
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def key(row: dict[str, Any]) -> str:
    base = row.get('match_key') or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}"
    return '|'.join([
        norm(base),
        norm(row.get('family') or row.get('market_family') or row.get('market')),
        norm(row.get('selection') or row.get('selection_key')),
        str(row.get('point') or row.get('line') or '').strip(),
    ])


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in ('reasons', 'reject_reasons', 'quality_reasons'):
        val = row.get(name)
        if isinstance(val, list):
            out.extend(str(x) for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            out.extend(x.strip() for x in re.split(r'[,|;/]+', val) if x.strip())
    return out


def collect(path: Path) -> dict[str, dict[str, Any]]:
    rows = candidate_rows(load_json(path, None))
    out = {}
    for row in rows:
        row = dict(row)
        row['_artifact'] = path.name
        out.setdefault(key(row), row)
    return out


def main() -> int:
    before = collect(EXPORT_DIR / 'latest-candidates-before-quality.json')
    after = collect(EXPORT_DIR / 'latest-candidates-after-quality.json')
    fallback = collect(EXPORT_DIR / 'latest-controlled-fallback-report.json')
    quality = collect(EXPORT_DIR / 'latest-quality-consensus-safe-relief.json')
    api = collect(EXPORT_DIR / 'latest-api-coverage-consensus-runtime-patch.json')
    all_keys = sorted(set(before) | set(after) | set(fallback) | set(quality) | set(api))
    rows: list[dict[str, Any]] = []
    reason_counts = Counter()
    negative_after = 0
    for k in all_keys:
        b = before.get(k, {})
        a = after.get(k, {}) or quality.get(k, {}) or fallback.get(k, {}) or api.get(k, {})
        ev_before = as_float(b.get('ev_pct') or b.get('ev') or b.get('canonical_ev_pct'))
        ev_after = as_float(a.get('ev_pct') or a.get('ev') or a.get('canonical_ev_pct'))
        edge_before = as_float(b.get('edge_pct') or b.get('edge_pp') or b.get('canonical_edge_pp'))
        edge_after = as_float(a.get('edge_pct') or a.get('edge_pp') or a.get('canonical_edge_pp'))
        r = reasons(a) or reasons(b)
        for item in r:
            reason_counts[item] += 1
        negative_after += int(ev_after is not None and ev_after < 0)
        rows.append({
            'key': k,
            'match_key': b.get('match_key') or a.get('match_key'),
            'home_team': b.get('home_team') or a.get('home_team'),
            'away_team': b.get('away_team') or a.get('away_team'),
            'family': b.get('family') or a.get('family') or b.get('market_family') or a.get('market_family'),
            'selection': b.get('selection') or a.get('selection'),
            'point': b.get('point') or a.get('point') or b.get('line') or a.get('line'),
            'odds': b.get('odds') or a.get('odds') or b.get('selected_odds') or a.get('selected_odds'),
            'ev_before_pct': ev_before,
            'ev_after_pct': ev_after,
            'edge_before_pp': edge_before,
            'edge_after_pp': edge_after,
            'ev_delta_pct': round(ev_after - ev_before, 4) if ev_before is not None and ev_after is not None else None,
            'edge_delta_pp': round(edge_after - edge_before, 4) if edge_before is not None and edge_after is not None else None,
            'quality': a.get('quality') or a.get('quality_score') or b.get('quality') or b.get('quality_score'),
            'reasons': r,
            'stage_seen': {
                'before_quality': k in before,
                'after_quality': k in after,
                'fallback': k in fallback,
                'quality_relief': k in quality,
                'api_coverage': k in api,
            },
        })
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID') or '',
        'counts': {
            'candidate_keys': len(all_keys),
            'before_quality': len(before),
            'after_quality': len(after),
            'fallback': len(fallback),
            'negative_ev_after_calibration': negative_after,
        },
        'top_reasons': reason_counts.most_common(20),
        'rows': rows[:200],
        'notes': ['This is an audit only. It does not change candidate probabilities, EV, or publication guards.'],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
