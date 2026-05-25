from __future__ import annotations

"""Append run candidates to a forward-test ledger.

Robust accumulation layer:
* handles fallback rows where metrics are nested under ``metrics``;
* keeps rejected/watch-only candidates even when nothing is published;
* never crashes because of legacy ``norm``/``_norm`` naming drift;
* writes a summary artifact on every run.
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


# Backward/forward alias: older patched versions sometimes called _norm().
_norm = norm


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


def deep_get(row: dict[str, Any], *names: str) -> Any:
    """Read a value from row, nested metrics, source_summary, diagnostics, or contract."""
    containers: list[Any] = [row]
    for key in ('metrics', 'source_summary', 'diagnostics', 'metadata', 'publish_coverage_contract', 'harizon_contract'):
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
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in (
        'candidates', 'rows', 'data', 'evaluated', 'blocked_top', 'near_miss',
        'watchlist', 'selected', 'selected_all', 'published_candidates',
        'candidates_before_quality', 'passed_candidates',
    ):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(val)
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(rows(decision))
    return out


def identity(row: dict[str, Any]) -> str:
    base = deep_get(row, 'match_key', 'canonical_match_id') or (
        f"{deep_get(row, 'home_team')}|{deep_get(row, 'away_team')}|"
        f"{deep_get(row, 'kickoff_utc', 'commence_time')}"
    )
    family = deep_get(row, 'family', 'market_family', 'market')
    selection = deep_get(row, 'selection', 'selection_key')
    point = deep_get(row, 'point', 'line')
    return '|'.join([norm(base), norm(family), norm(selection), str(point or '').strip()])


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for container in (row, row.get('metrics') if isinstance(row.get('metrics'), dict) else {}):
        for name in ('reasons', 'reject_reasons', 'reject_reasons_ru', 'quality_reasons', 'publish_coverage_reasons'):
            val = container.get(name) if isinstance(container, dict) else None
            out.extend(list_from_any(val))
    # preserve order, remove duplicates
    seen: set[str] = set()
    result: list[str] = []
    for item in out:
        key = item.lower()
        if key not in seen:
            seen.add(key)
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


def _row_status(row: dict[str, Any], reason_list: list[str]) -> str:
    if row.get('telegram_sent') is True or row.get('published') is True or row.get('sent') is True:
        return 'published'
    joined = ' '.join(reason_list).lower()
    if 'watch' in joined or str(deep_get(row, 'tier') or '').lower() == 'c':
        return 'watch_only'
    return 'rejected_or_watch'


def collect_current_rows() -> list[dict[str, Any]]:
    sources = [
        ('before_quality', EXPORT_DIR / 'latest-candidates-before-quality.json'),
        ('after_quality', EXPORT_DIR / 'latest-candidates-after-quality.json'),
        ('fallback', EXPORT_DIR / 'latest-controlled-fallback-report.json'),
        ('quality_relief', EXPORT_DIR / 'latest-quality-consensus-safe-relief.json'),
        ('api_coverage', EXPORT_DIR / 'latest-api-coverage-consensus-runtime-patch.json'),
        ('normalized_publication', EXPORT_DIR / 'latest-normalized-publication-payloads.json'),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)
    for stage, path in sources:
        for row in rows(load_json(path, None)):
            k = identity(row)
            if not k or k == 'none|none|none|':
                continue
            current = merged.setdefault(k, {})
            # Keep non-empty values; nested dicts such as metrics are important.
            for kk, vv in row.items():
                if vv not in (None, '', [], {}):
                    current[kk] = vv
            stage_seen[k].add(stage)

    out: list[dict[str, Any]] = []
    run_id = os.getenv('GITHUB_RUN_ID') or datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    for k, row in merged.items():
        r = reasons(row)
        out.append({
            'ledger_id': f'{run_id}|{k}',
            'run_id': run_id,
            'created_at_utc': datetime.now(UTC).isoformat(),
            'candidate_key': k,
            'status': _row_status(row, r),
            'stage_seen': sorted(stage_seen[k]),
            'match_key': deep_get(row, 'match_key', 'canonical_match_id'),
            'home_team': deep_get(row, 'home_team'),
            'away_team': deep_get(row, 'away_team'),
            'league_name': deep_get(row, 'league_name'),
            'kickoff_utc': deep_get(row, 'kickoff_utc', 'commence_time'),
            'family': deep_get(row, 'family', 'market_family', 'market'),
            'selection': deep_get(row, 'selection', 'selection_key'),
            'point': deep_get(row, 'point', 'line'),
            'odds': as_float(deep_get(row, 'odds', 'selected_odds')),
            'ev_pct': as_float(deep_get(row, 'canonical_ev_pct', 'ev_pct', 'ev')),
            'edge_pp': as_float(deep_get(row, 'canonical_edge_pp', 'edge_pct', 'edge_pp')),
            'confidence': as_float(deep_get(row, 'confidence')),
            'quality': as_float(deep_get(row, 'quality', 'quality_score', 'quality_score_raw')),
            'odds_sources_count': as_int(deep_get(row, 'odds_sources_count', 'independent_odds_sources_count', 'exact_sources_count')),
            'context_sources_count': as_int(deep_get(row, 'context_sources_count', 'confirmation_sources_count')),
            'books_count': as_int(deep_get(row, 'books_count', 'exact_books_count')),
            'reasons': r,
            'settlement': row.get('settlement') if isinstance(row.get('settlement'), dict) else {},
        })
    return out


def summarize() -> dict[str, Any]:
    rows_existing: list[dict[str, Any]] = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding='utf-8').splitlines():
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows_existing.append(obj)
            except Exception:
                pass
    by_status = Counter(str(r.get('status') or 'unknown') for r in rows_existing)
    by_reason = Counter()
    missing_metrics = 0
    for row in rows_existing:
        if row.get('ev_pct') is None and row.get('edge_pp') is None and row.get('quality') is None:
            missing_metrics += 1
        for reason in row.get('reasons') or []:
            by_reason[str(reason)] += 1
    return {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'ledger_path': str(LEDGER),
        'total_rows': len(rows_existing),
        'by_status': dict(by_status),
        'rows_missing_core_metrics': missing_metrics,
        'top_reasons': by_reason.most_common(30),
        'notes': [
            'Use this ledger for forward testing before trusting ROI.',
            'Published/rejected/watch-only rows are accumulated; settlement fields can be filled by a future results job.',
        ],
    }


def main() -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    seen = existing_ids()
    new_rows = [r for r in collect_current_rows() if r['ledger_id'] not in seen]
    if new_rows:
        with LEDGER.open('a', encoding='utf-8') as f:
            for row in new_rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    summary = summarize()
    summary['new_rows_added'] = len(new_rows)
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
