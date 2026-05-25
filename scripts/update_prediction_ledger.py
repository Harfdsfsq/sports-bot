from __future__ import annotations

"""Append run candidates to a forward-test ledger.

The ledger is the transition from patch-chasing to measurable forecasting:
every candidate that reaches discovery/quality/fallback is stored with its odds,
EV, reasons, publication status and later settlement fields if available.
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


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('candidates', 'rows', 'data', 'evaluated', 'blocked_top', 'near_miss', 'selected', 'selected_all', 'published_candidates'):
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
    base = row.get('match_key') or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}"
    return '|'.join([
        norm(base), norm(row.get('family') or row.get('market_family') or row.get('market')),
        norm(row.get('selection') or row.get('selection_key')), str(row.get('point') or row.get('line') or '').strip(),
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


def collect_current_rows() -> list[dict[str, Any]]:
    sources = [
        ('before_quality', EXPORT_DIR / 'latest-candidates-before-quality.json'),
        ('after_quality', EXPORT_DIR / 'latest-candidates-after-quality.json'),
        ('fallback', EXPORT_DIR / 'latest-controlled-fallback-report.json'),
        ('normalized_publication', EXPORT_DIR / 'latest-normalized-publication-payloads.json'),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)
    for stage, path in sources:
        for row in rows(load_json(path, None)):
            k = identity(row)
            current = merged.setdefault(k, {})
            current.update({kk: vv for kk, vv in row.items() if vv not in (None, '', [], {})})
            stage_seen[k].add(stage)
    out = []
    run_id = os.getenv('GITHUB_RUN_ID') or datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    for k, row in merged.items():
        r = reasons(row)
        status = 'published' if row.get('telegram_sent') is True or row.get('published') is True else 'rejected_or_watch'
        if any('watch' in x.lower() for x in r):
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
            'family': row.get('family') or row.get('market_family') or row.get('market'),
            'selection': row.get('selection') or row.get('selection_key'),
            'point': row.get('point') or row.get('line'),
            'odds': as_float(row.get('odds') or row.get('selected_odds')),
            'ev_pct': as_float(row.get('ev_pct') or row.get('ev')),
            'edge_pp': as_float(row.get('edge_pct') or row.get('edge_pp')),
            'confidence': as_float(row.get('confidence')),
            'quality': as_float(row.get('quality') or row.get('quality_score')),
            'odds_sources_count': row.get('odds_sources_count') or row.get('independent_odds_sources_count'),
            'context_sources_count': row.get('context_sources_count') or row.get('confirmation_sources_count'),
            'books_count': row.get('books_count'),
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
    for row in rows_existing:
        for reason in row.get('reasons') or []:
            by_reason[str(reason)] += 1
    return {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'ledger_path': str(LEDGER),
        'total_rows': len(rows_existing),
        'by_status': dict(by_status),
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
