from __future__ import annotations

"""Build a before/after calibration audit for every candidate row we can see.

Robust version: fallback/api/quality artifacts often keep metrics nested under
``metrics``; this script reads those nested values so the audit is useful for
forward testing.
"""

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


def deep_get(row: dict[str, Any], *names: str) -> Any:
    containers: list[Any] = [row]
    for key in ('metrics', 'source_summary', 'diagnostics', 'metadata', 'publish_coverage_contract'):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for container in containers:
        if not isinstance(container, dict):
            continue
        for name in names:
            if container.get(name) not in (None, '', [], {}):
                return container.get(name)
    return None


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


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
    base = deep_get(row, 'match_key', 'canonical_match_id') or (
        f"{deep_get(row, 'home_team')}|{deep_get(row, 'away_team')}|{deep_get(row, 'kickoff_utc', 'commence_time')}"
    )
    return '|'.join([
        norm(base),
        norm(deep_get(row, 'family', 'market_family', 'market')),
        norm(deep_get(row, 'selection', 'selection_key')),
        str(deep_get(row, 'point', 'line') or '').strip(),
    ])


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for container in (row, row.get('metrics') if isinstance(row.get('metrics'), dict) else {}):
        for name in ('reasons', 'reject_reasons', 'reject_reasons_ru', 'quality_reasons', 'publish_coverage_reasons'):
            val = container.get(name) if isinstance(container, dict) else None
            out.extend(list_from_any(val))
    seen = set()
    result = []
    for item in out:
        marker = item.lower()
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def collect(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidate_rows(load_json(path, None)):
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
        # Prefer the latest/strictest post-calibration artifact for after values.
        a = after.get(k, {}) or quality.get(k, {}) or fallback.get(k, {}) or api.get(k, {})
        ev_before = as_float(deep_get(b, 'canonical_ev_pct', 'ev_pct', 'ev'))
        ev_after = as_float(deep_get(a, 'canonical_ev_pct', 'ev_pct', 'ev'))
        edge_before = as_float(deep_get(b, 'canonical_edge_pp', 'edge_pct', 'edge_pp', 'market_edge_pp'))
        edge_after = as_float(deep_get(a, 'canonical_edge_pp', 'edge_pct', 'edge_pp', 'market_edge_pp'))
        r = reasons(a) or reasons(b)
        for item in r:
            reason_counts[item] += 1
        negative_after += int(ev_after is not None and ev_after < 0)
        rows.append({
            'key': k,
            'match_key': deep_get(b, 'match_key', 'canonical_match_id') or deep_get(a, 'match_key', 'canonical_match_id'),
            'home_team': deep_get(b, 'home_team') or deep_get(a, 'home_team'),
            'away_team': deep_get(b, 'away_team') or deep_get(a, 'away_team'),
            'family': deep_get(b, 'family', 'market_family', 'market') or deep_get(a, 'family', 'market_family', 'market'),
            'selection': deep_get(b, 'selection', 'selection_key') or deep_get(a, 'selection', 'selection_key'),
            'point': deep_get(b, 'point', 'line') or deep_get(a, 'point', 'line'),
            'odds': deep_get(b, 'odds', 'selected_odds') or deep_get(a, 'odds', 'selected_odds'),
            'ev_before_pct': ev_before,
            'ev_after_pct': ev_after,
            'edge_before_pp': edge_before,
            'edge_after_pp': edge_after,
            'ev_delta_pct': round(ev_after - ev_before, 4) if ev_before is not None and ev_after is not None else None,
            'edge_delta_pp': round(edge_after - edge_before, 4) if edge_before is not None and edge_after is not None else None,
            'quality': deep_get(a, 'quality', 'quality_score', 'quality_score_raw') or deep_get(b, 'quality', 'quality_score', 'quality_score_raw'),
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
