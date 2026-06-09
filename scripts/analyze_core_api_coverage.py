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


def as_int(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _run_retro_price_audit() -> dict[str, Any]:
    try:
        from scripts.retro_audit_price_integrity_ledger import main as retro_main
        retro_main([])
        report = load(EXPORT / 'latest-ledger-retro-price-integrity-audit.json', {})
        return report if isinstance(report, dict) else {'status': 'bad_report_shape'}
    except Exception as exc:
        return {'status': 'failed', 'error': str(exc)[:300]}


def main() -> int:
    report = load(EXPORT / 'latest-harizon-telegram-run-report.json', {})
    truth = load(EXPORT / 'latest-day-inventory-coverage-truth.json', {})
    normalizer = load(EXPORT / 'latest-bookmaker-quorum-coverage-normalizer.json', {})
    backfill = load(EXPORT / 'latest-odds-api-bookmaker-quorum-mapping-backfill.json', {})
    snapshot = load(EXPORT / 'latest-odds-api-io-offer-snapshot.json', {})
    max_policy = load(EXPORT / 'latest-api-maximum-coverage-runtime-policy.json', {})
    timing = load(EXPORT / 'latest-controlled-fallback-publication-timing-guard.json', {})
    price_guard = load(EXPORT / 'latest-controlled-fallback-price-integrity-guard.json', {})
    bzz_gap = load(EXPORT / 'latest-bzzoiro-context-gap-finalizer.json', {})
    bzz_gap_install = load(EXPORT / 'latest-bzzoiro-context-gap-finalizer-install.json', {})
    retro_audit = _run_retro_price_audit()

    api = report.get('api') if isinstance(report.get('api'), dict) else {}
    coverage = report.get('coverage') if isinstance(report.get('coverage'), dict) else {}
    counts = truth.get('counts') if isinstance(truth.get('counts'), dict) else {}
    if not counts and isinstance(normalizer.get('counts'), dict):
        counts = normalizer['counts']
    windows = normalizer.get('window_counts') if isinstance(normalizer.get('window_counts'), dict) else {}
    near = windows.get('0-4') if isinstance(windows.get('0-4'), dict) else {}

    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok',
        'audit_source': 'manual_or_workflow_script',
        'inventory': {
            'matches_total': as_int(get(counts, 'matches_total') or get(coverage, 'day_inventory_total')),
            'with_line': as_int(get(counts, 'matches_with_odds') or get(coverage, 'day_inventory_with_odds')),
            'with_context': as_int(get(counts, 'matches_with_context') or get(coverage, 'day_inventory_with_context')),
            'with_2plus_bookmakers': as_int(get(counts, 'matches_with_2plus_price_confirmations')),
            'with_2plus_context': as_int(get(counts, 'matches_with_2plus_context_sources')),
            'ready_for_model': as_int(get(counts, 'matches_ready_for_model')),
        },
        'near_window_0_4h': near,
        'api': {
            'odds_api_io': api.get('odds_api_io', {}),
            'bzzoiro': api.get('bzzoiro', {}),
            'sstats': api.get('sstats', {}),
            'sportlogic': api.get('sportlogic', {}),
        },
        'snapshot': {
            'rows_count': as_int(snapshot.get('rows_count')),
            'matches_count': as_int(snapshot.get('matches_count')),
            'matches_with_2plus_books_same_side_market': as_int(snapshot.get('matches_with_2plus_books_same_side_market')),
        },
        'backfill': {
            'mapped_matches': as_int(backfill.get('mapped_matches')),
            'changed_inventory_rows': as_int(backfill.get('changed_inventory_rows')),
            'changed_truth_rows': as_int(backfill.get('changed_truth_rows')),
            'offer_rows_from_snapshot': as_int(backfill.get('offer_rows_from_snapshot')),
        },
        'guards': {
            'timing_deferred_total': as_int(timing.get('deferred_total')),
            'price_integrity_removed_total': as_int(price_guard.get('removed_total')),
        },
        'ledger_retro_price_audit': {
            'status': retro_audit.get('status') if isinstance(retro_audit, dict) else '',
            'mutated': bool(retro_audit.get('mutated')) if isinstance(retro_audit, dict) else False,
            'published_flagged': as_int(retro_audit.get('published_flagged')) if isinstance(retro_audit, dict) else 0,
            'pending_flagged': as_int(retro_audit.get('pending_flagged')) if isinstance(retro_audit, dict) else 0,
            'changed_rows': as_int(retro_audit.get('changed_rows')) if isinstance(retro_audit, dict) else 0,
            'policy': retro_audit.get('policy') if isinstance(retro_audit, dict) else '',
        },
        'bzzoiro_context_gap': {
            'runtime': bzz_gap,
            'install': bzz_gap_install,
        },
        'max_policy_installed': bool(max_policy),
        'bottlenecks': [],
    }
    if as_int(get(payload, 'inventory', 'with_2plus_context')) < max(1, as_int(get(payload, 'inventory', 'with_2plus_bookmakers')) // 2):
        payload['bottlenecks'].append('2plus_context_below_bookmaker_quorum')
    if as_int(near.get('bookmaker_2plus')) > 0 and as_int(near.get('context_2plus')) < as_int(near.get('bookmaker_2plus')):
        payload['bottlenecks'].append('near_window_context_gap')
    if as_int(get(payload, 'api', 'sportlogic', 'matched')) <= 0 and as_int(get(payload, 'api', 'sportlogic', 'fixtures_fetched')) <= 0:
        payload['bottlenecks'].append('sportlogic_no_fixtures_or_matches')
    if as_int(get(payload, 'api', 'bzzoiro', 'secondary_offers_added')) <= 0:
        payload['bottlenecks'].append('bzzoiro_secondary_offers_zero')
    if as_int(get(payload, 'api', 'sstats', 'team_form_contexts')) <= 0:
        payload['bottlenecks'].append('sstats_team_form_zero')
    if as_int(get(payload, 'ledger_retro_price_audit', 'pending_flagged')) or as_int(get(payload, 'ledger_retro_price_audit', 'published_flagged')):
        payload['bottlenecks'].append('ledger_price_integrity_retro_flags_present')

    payload['next_actions'] = [
        'Do not relax publication guards.',
        'Prioritize 0-4h/4-8h bookmaker-qualified matches for context gap filling.',
        'If SportLogic remains 0 fixtures, verify base URL/auth/date parameters outside publication flow.',
        'Keep Bzzoiro as context/confirmation until secondary offers parser becomes non-zero.',
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
