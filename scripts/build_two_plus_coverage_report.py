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
    for box in (
        row,
        row.get('metrics') if isinstance(row.get('metrics'), dict) else {},
        row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {},
        row.get('diagnostics') if isinstance(row.get('diagnostics'), dict) else {},
    ):
        if not isinstance(box, dict):
            continue
        for key in keys:
            value = box.get(key)
            if value not in (None, ''):
                return num(value)
    return 0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        value = payload.get('rows') or payload.get('matches') or payload.get('items')
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _count_rows(rows: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for row in rows:
        value = metric(row, *keys)
        if value >= 2:
            total += 1
    return total


def _count_value(counts: dict[str, Any], row_values: list[int], *keys: str) -> int:
    for key in keys:
        value = counts.get(key)
        if value not in (None, ''):
            return num(value)
    return max(row_values or [0])


def _strict_counts(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get('counts')
    if isinstance(value, dict):
        return value
    required = {
        'matches_total',
        'matches_with_2plus_odds_sources',
        'matches_with_2plus_context_sources',
    }
    return payload if required.intersection(payload) else {}


def main() -> int:
    truth = load(EXPORT / 'latest-day-inventory-coverage-truth.json', {})
    strict_sync = load(EXPORT / 'latest-strict-coverage-inventory-sync.json', {})
    diag = load(EXPORT / 'latest-fresh-b-cover-diagnostics.json', {})
    fallback = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    bzz = load(EXPORT / 'latest-bzzoiro-context-gap-finalizer.json', {})
    expander = load(EXPORT / 'latest-bzzoiro-v2-gap-plan-expander.json', {})
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
    truth_counts = truth.get('counts') if isinstance(truth.get('counts'), dict) else (truth if isinstance(truth, dict) else {})
    strict_counts = _strict_counts(strict_sync)
    # The strict sync is the authoritative final classifier. It reads persisted API
    # evidence and excludes aliases/proxy rows. Truth row recounts are diagnostic only
    # because generated inventory files can be rewritten after the sync step.
    counts = strict_counts or truth_counts
    inv_rows = _rows(truth)
    row_odds_2plus = _count_rows(inv_rows, 'odds_sources_count', 'odds_source_count', 'independent_odds_sources_count')
    row_ctx_2plus = _count_rows(inv_rows, 'context_sources_count', 'context_source_count', 'confirmation_sources_count')
    total = num(counts.get('matches_total') or counts.get('total') or counts.get('inventory_rows') or len(inv_rows) or 300, 300) if isinstance(counts, dict) else (len(inv_rows) or 300)
    odds_2plus = _count_value(
        counts if isinstance(counts, dict) else {},
        [row_odds_2plus],
        'matches_with_2plus_odds_sources',
        'odds_2plus_sources',
        'two_plus_independent_odds_source',
        'with_2plus_odds_sources',
    )
    ctx_2plus = _count_value(
        counts if isinstance(counts, dict) else {},
        [row_ctx_2plus],
        'matches_with_2plus_context_sources',
        'context_2plus_sources',
        'two_plus_contexts',
        'with_2plus_context_sources',
    )
    bzz_stats = bzz.get('stats') if isinstance(bzz.get('stats'), dict) else {}
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'target 2+ independent line/odds sources and 2+ context sources for Telegram publication',
        'authoritative_count_source': 'latest-strict-coverage-inventory-sync.json' if strict_counts else 'coverage_truth_fallback',
        'inventory': {
            'total': total,
            'odds_2plus_sources': odds_2plus,
            'context_2plus_sources': ctx_2plus,
            'odds_2plus_gap': max(0, total - odds_2plus),
            'context_2plus_gap': max(0, total - ctx_2plus),
            'row_recount_odds_2plus_sources': row_odds_2plus,
            'row_recount_context_2plus_sources': row_ctx_2plus,
        },
        'bzzoiro_v2': {
            'requests': bzz_stats.get('requests'),
            'events_fetched': bzz_stats.get('events_fetched') or bzz_stats.get('v2_events_fetched'),
            'contexts_added': bzz_stats.get('contexts_added_total') or bzz_stats.get('contexts_added'),
            'odds_hints': bzz_stats.get('odds_hints'),
            'odds_comparison_attempted': bzz_stats.get('odds_comparison_attempted'),
            'odds_comparison_attached': bzz_stats.get('odds_comparison_attached'),
            'gap_expander_added': expander.get('added') if isinstance(expander, dict) else None,
            'gap_expander_output_matches': expander.get('output_matches') if isinstance(expander, dict) else None,
        },
        'fresh_b_cover': {
            'rows': diag.get('b_cover_rows') or diag.get('rows'),
            'with_current_offer': diag.get('b_cover_with_any_current_offer_match') or diag.get('active_b_cover_with_any_current_offer_match') or diag.get('with_current_offer') or diag.get('active_with_offer'),
            'fresh_buckets': diag.get('current_market_buckets_totals_spreads') or diag.get('fresh_buckets'),
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
