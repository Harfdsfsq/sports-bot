from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-core-api-coverage-audit.json'


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def get(d: Any, *keys: str, default: Any = 0) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def main() -> int:
    report = load(EXPORT / 'latest-harizon-telegram-run-report.json', {})
    truth = load(EXPORT / 'latest-day-inventory-coverage-truth.json', {})
    backfill = load(EXPORT / 'latest-odds-api-bookmaker-quorum-mapping-backfill.json', {})
    snapshot = load(EXPORT / 'latest-odds-api-io-offer-snapshot.json', {})
    max_policy = load(EXPORT / 'latest-api-maximum-coverage-runtime-policy.json', {})
    api = report.get('api') if isinstance(report.get('api'), dict) else {}
    coverage = report.get('coverage') if isinstance(report.get('coverage'), dict) else {}
    counts = truth.get('counts') if isinstance(truth.get('counts'), dict) else {}

    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok',
        'inventory': {
            'matches_total': get(counts, 'matches_total') or get(coverage, 'day_inventory_total'),
            'with_line': get(counts, 'matches_with_odds') or get(coverage, 'day_inventory_with_odds'),
            'with_context': get(counts, 'matches_with_context') or get(coverage, 'day_inventory_with_context'),
            'with_2plus_bookmakers': get(counts, 'matches_with_2plus_price_confirmations'),
            'with_2plus_context': get(counts, 'matches_with_2plus_context_sources'),
            'ready_for_model': get(counts, 'matches_ready_for_model'),
        },
        'api': {
            'odds_api_io': api.get('odds_api_io', {}),
            'bzzoiro': api.get('bzzoiro', {}),
            'sstats': api.get('sstats', {}),
            'sportlogic': api.get('sportlogic', {}),
        },
        'snapshot': {
            'rows_count': snapshot.get('rows_count'),
            'matches_count': snapshot.get('matches_count'),
            'matches_with_2plus_books_same_side_market': snapshot.get('matches_with_2plus_books_same_side_market'),
        },
        'backfill': {
            'mapped_matches': backfill.get('mapped_matches'),
            'changed_inventory_rows': backfill.get('changed_inventory_rows'),
            'changed_truth_rows': backfill.get('changed_truth_rows'),
            'offer_rows_from_snapshot': backfill.get('offer_rows_from_snapshot'),
        },
        'max_policy_installed': bool(max_policy),
        'bottlenecks': [],
    }
    if int(get(payload, 'inventory', 'with_2plus_context', default=0) or 0) < int(get(payload, 'inventory', 'with_2plus_bookmakers', default=0) or 0):
        payload['bottlenecks'].append('context_2plus_below_bookmaker_2plus')
    if int(get(payload, 'api', 'sportlogic', 'matched', default=0) or 0) <= 0:
        payload['bottlenecks'].append('sportlogic_no_matched_fixtures')
    if int(get(payload, 'api', 'bzzoiro', 'secondary_offers_added', default=0) or 0) <= 0:
        payload['bottlenecks'].append('bzzoiro_secondary_offers_zero')
    if int(get(payload, 'api', 'sstats', 'team_form_contexts', default=0) or 0) <= 0:
        payload['bottlenecks'].append('sstats_team_form_zero')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
