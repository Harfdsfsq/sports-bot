from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-two-plus-coverage-report.json'
ART = Path('artifacts/run-bot/latest-two-plus-coverage-report.json')


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def num(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return default


def candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        out: list[dict[str, Any]] = []
        for key in ('evaluated', 'evaluated_candidates', 'selected_all', 'candidates', 'rows', 'items'):
            value = payload.get(key)
            if isinstance(value, dict):
                out.append(value)
            elif isinstance(value, list):
                out.extend(x for x in value if isinstance(x, dict))
        return out
    return []


def metric(row: dict[str, Any], *keys: str) -> int:
    for box in (row, row.get('metrics') if isinstance(row.get('metrics'), dict) else {}, row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}, row.get('diagnostics') if isinstance(row.get('diagnostics'), dict) else {}):
        if not isinstance(box, dict):
            continue
        for key in keys:
            value = box.get(key)
            if value not in (None, ''):
                return num(value)
    return 0


def main() -> int:
    truth = load(EXPORT / 'latest-day-inventory-coverage-truth.json', {})
    diag = load(EXPORT / 'latest-fresh-b-cover-diagnostics.json', {})
    fallback = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    rows = candidates(fallback)
    candidate_gaps: list[dict[str, Any]] = []
    for row in rows[:100]:
        odds = metric(row, 'odds_sources_count', 'line_sources_count', 'price_sources_count', 'sources_count')
        ctx = metric(row, 'context_sources_count', 'confirmation_sources_count', 'sources_count')
        if odds < 2 or ctx < 2:
            candidate_gaps.append({
                'home_team': row.get('home_team') or row.get('home'),
                'away_team': row.get('away_team') or row.get('away'),
                'selection': row.get('selection') or row.get('selection_key'),
                'point': row.get('point') or row.get('line'),
                'odds_sources_count': odds,
                'context_sources_count': ctx,
                'reasons': row.get('reasons') or row.get('reject_reasons'),
            })
    counts = truth.get('counts') if isinstance(truth.get('counts'), dict) else truth
    total = num(counts.get('matches_total') or counts.get('total') or counts.get('inventory_rows') or 300, 300) if isinstance(counts, dict) else 300
    odds_2plus = num((counts or {}).get('odds_2plus_sources') or (counts or {}).get('two_plus_independent_odds_source') or (counts or {}).get('with_2plus_odds_sources')) if isinstance(counts, dict) else 0
    ctx_2plus = num((counts or {}).get('context_2plus_sources') or (counts or {}).get('two_plus_contexts') or (counts or {}).get('with_2plus_context_sources')) if isinstance(counts, dict) else 0
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'target 2+ independent line/odds sources and 2+ context sources for Telegram publication',
        'inventory': {
            'total': total,
            'odds_2plus_sources': odds_2plus,
            'context_2plus_sources': ctx_2plus,
            'odds_2plus_gap': max(0, total - odds_2plus),
            'context_2plus_gap': max(0, total - ctx_2plus),
        },
        'fresh_b_cover': {
            'rows': diag.get('b_cover_rows') or diag.get('rows'),
            'with_current_offer': diag.get('with_current_offer') or diag.get('active_with_offer'),
            'fresh_buckets': diag.get('fresh_buckets'),
        } if isinstance(diag, dict) else {},
        'fallback_candidate_gap_count': len(candidate_gaps),
        'fallback_candidate_gap_sample': candidate_gaps[:20],
        'next_actions': [
            'If odds_2plus_gap remains high, inspect Odds API IO account2 and Bzzoiro/SportLogic overlap.',
            'If context_2plus_gap remains high, inspect Bzzoiro v2 stats/prediction and SStats deep enrichment.',
            'Telegram fallback is expected to reject candidates with <2 line/context sources while this policy is enabled.',
        ],
    }
    for path in (OUT, ART):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'odds_2plus_gap': payload['inventory']['odds_2plus_gap'], 'context_2plus_gap': payload['inventory']['context_2plus_gap']}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
