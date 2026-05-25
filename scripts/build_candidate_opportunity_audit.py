from __future__ import annotations

"""Audit why coverage-ready matches do or do not become bet candidates.

This script is intentionally read-only: it does not publish, rerank, or relax
any guard.  It gives the next run a stable artifact that connects the unified
300-match inventory contract to the CandidateFactory/fallback outputs.
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
OUT = EXPORT_DIR / 'latest-candidate-opportunity-audit.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').lower().strip()
    text = re.sub(r'^soccer\|', '', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(text.split())


def row_key(row: dict[str, Any]) -> str:
    key = row.get('match_key') or row.get('canonical_match_id')
    if key:
        return norm(key)
    return norm(f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}")


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in (
        'candidates', 'rows', 'data', 'selected_all', 'evaluated', 'blocked_top',
        'near_miss', 'candidates_before_quality', 'passed_candidates',
        'publishable_candidates', 'selected', 'sample',
    ):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(val)
    decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def load_candidates() -> tuple[list[dict[str, Any]], dict[str, int]]:
    paths = [
        EXPORT_DIR / 'latest-candidates-before-quality.json',
        EXPORT_DIR / 'latest-candidates-after-quality.json',
        EXPORT_DIR / 'latest-rescue-candidates.json',
        EXPORT_DIR / 'latest-controlled-fallback-report.json',
        EXPORT_DIR / 'latest-candidate-value-runtime-patch.json',
        EXPORT_DIR / 'latest-quality-consensus-safe-relief.json',
        EXPORT_DIR / 'latest-api-coverage-consensus-runtime-patch.json',
    ]
    rows: list[dict[str, Any]] = []
    by_path: dict[str, int] = {}
    for path in paths:
        payload = load_json(path, None)
        candidates = candidate_rows(payload)
        by_path[path.name] = len(candidates)
        for row in candidates:
            row = dict(row)
            row['_artifact'] = path.name
            rows.append(row)
    return rows, by_path


def candidate_key(row: dict[str, Any]) -> str:
    key = row.get('match_key') or row.get('canonical_match_id')
    if key:
        return norm(key)
    return norm(f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc') or row.get('commence_time')}")


def candidate_market_key(row: dict[str, Any]) -> str:
    return '|'.join([
        candidate_key(row),
        norm(row.get('family') or row.get('market_family') or row.get('market')),
        norm(row.get('selection') or row.get('selection_key')),
        str(row.get('point') or row.get('line') or '').strip(),
    ])


def main() -> int:
    truth = load_json(EXPORT_DIR / 'latest-day-inventory-coverage-truth.json', {})
    truth_rows = [r for r in truth.get('rows', []) if isinstance(r, dict)] if isinstance(truth, dict) else []
    candidates, by_path = load_candidates()
    candidate_matches = defaultdict(list)
    candidate_markets: set[str] = set()
    for row in candidates:
        candidate_matches[candidate_key(row)].append(row)
        candidate_markets.add(candidate_market_key(row))

    strict_ready = [r for r in truth_rows if r.get('strict_ready_for_publish')]
    fresh_ready = [r for r in truth_rows if r.get('ready_for_publish')]
    ready_without_candidates: list[dict[str, Any]] = []
    ready_with_candidates = 0
    window_counters = Counter()
    for row in strict_ready:
        key = row_key(row)
        has_candidate = bool(candidate_matches.get(key))
        ready_with_candidates += int(has_candidate)
        try:
            minutes = float(row.get('minutes_to_kickoff'))
        except Exception:
            minutes = 999999.0
        if 0 <= minutes <= 4 * 60:
            window_counters['strict_ready_0_4h'] += 1
        if 0 <= minutes <= 12 * 60:
            window_counters['strict_ready_0_12h'] += 1
        if not has_candidate and len(ready_without_candidates) < 30:
            ready_without_candidates.append({
                'match_key': row.get('match_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'kickoff_utc': row.get('kickoff_utc'),
                'minutes_to_kickoff': row.get('minutes_to_kickoff'),
                'odds_sources_count': row.get('odds_sources_count'),
                'price_confirmations': row.get('price_confirmations'),
                'context_sources_count': row.get('context_sources_count'),
                'missing': row.get('missing') or [],
                'likely_gap': 'coverage_ready_but_no_market_value_candidate',
            })

    duplicate_markets = len(candidates) - len(candidate_markets)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID') or '',
        'counts': {
            'coverage_truth_rows': len(truth_rows),
            'strict_ready_matches': len(strict_ready),
            'fresh_ready_matches': len(fresh_ready),
            'strict_ready_with_any_candidate': ready_with_candidates,
            'strict_ready_without_candidate': max(0, len(strict_ready) - ready_with_candidates),
            'candidate_rows_loaded': len(candidates),
            'unique_candidate_markets': len(candidate_markets),
            'duplicate_candidate_markets': max(0, duplicate_markets),
            **dict(window_counters),
        },
        'candidate_artifact_counts': by_path,
        'ready_without_candidates_sample': ready_without_candidates,
        'notes': [
            'A strict-ready match only means data coverage is enough; it still may have no positive value market.',
            'This audit bridges coverage truth to CandidateFactory/fallback candidates without changing publication logic.',
        ],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
