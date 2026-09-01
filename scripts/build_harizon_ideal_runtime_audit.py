from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-harizon-ideal-runtime-audit.json'
OUT_TXT = EXPORT / 'latest-harizon-ideal-runtime-audit.txt'


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def integer(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return default


def pct(a: int, b: int) -> float:
    return round(a / b * 100.0, 1) if b else 0.0


def rows_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ('evaluated', 'candidates', 'rows', 'items', 'near_misses'):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def metrics(row: dict[str, Any]) -> dict[str, Any]:
    m = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    c = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    if not m and isinstance(c.get('metrics'), dict):
        m = c.get('metrics')
    return m if isinstance(m, dict) else {}


def candidate(row: dict[str, Any]) -> dict[str, Any]:
    return row.get('candidate') if isinstance(row.get('candidate'), dict) else row


def reasons(row: dict[str, Any]) -> list[str]:
    c = candidate(row)
    out = row.get('reject_reasons') or row.get('reasons') or c.get('reject_reasons') or c.get('reasons') or []
    if isinstance(out, str):
        out = [out]
    return [str(x) for x in out if str(x).strip()]


def get_metric(row: dict[str, Any], *keys: str) -> float:
    c = candidate(row)
    m = metrics(row)
    for key in keys:
        if key in m:
            return num(m.get(key))
        if key in c:
            return num(c.get(key))
    return 0.0


def best_near_misses(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    scored: list[tuple[tuple[float, float, float, float], dict[str, Any]]] = []
    for row in rows:
        c = candidate(row)
        m = metrics(row)
        ev = max(num(m.get('canonical_ev_pct')), num(m.get('ev_pct')), num(c.get('ev_pct')))
        edge = max(num(m.get('canonical_edge_pp')), num(m.get('edge_pp')), num(c.get('edge_pp')))
        q = max(num(m.get('reserve_quality_score')), num(m.get('quality_score')), num(c.get('quality_score')), num(c.get('quality')))
        odds = max(num(m.get('odds')), num(c.get('odds')))
        if ev <= 0 and edge <= 0:
            continue
        hard = sum(1 for r in reasons(row) if any(tok in r for tok in ('odds_below_global_min', 'xg_direction_conflict', 'semantic_line_movement_failed', 'current price recheck value lost', 'final_ev_below_min')))
        item = {
            'home_team': c.get('home_team') or c.get('home'),
            'away_team': c.get('away_team') or c.get('away'),
            'league_name': c.get('league_name') or c.get('league'),
            'selection': c.get('selection') or c.get('market'),
            'point': c.get('point') or c.get('line'),
            'odds': odds,
            'ev_pct': ev,
            'edge_pp': edge,
            'quality': q,
            'odds_sources': integer(m.get('odds_sources_count') or c.get('odds_sources_count')),
            'books': integer(m.get('books_count') or c.get('books_count') or m.get('bookmaker_count')),
            'context_sources': integer(m.get('context_sources_count') or m.get('confirmation_sources_count') or c.get('context_sources_count')),
            'reasons': reasons(row)[:12],
        }
        scored.append((( -hard, ev, edge, q), item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def main() -> int:
    inventory = load(EXPORT / 'latest-day-inventory-summary.json', {})
    fallback = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    detailed = load(EXPORT / 'latest-detailed-run-report.json', {})
    debug = load(Path('.logs/debug-last-run.json'), {})
    line_diag = load(EXPORT / 'latest-line-movement-diagnostics.json', {})
    bzz_bridge = load(EXPORT / 'latest-bzzoiro-overlap-bridge.json', {})
    bzz_merge = load(EXPORT / 'latest-sstats-bzzoiro-odds-merge.json', {})
    proxy = load(EXPORT / 'latest-rescue-proxy-placeholder-xg-replacement.json', {})

    counts = inventory.get('counts') if isinstance(inventory.get('counts'), dict) else {}
    total = integer(counts.get('matches_total'))
    with_odds = integer(counts.get('matches_with_odds'))
    with_context = integer(counts.get('matches_with_context'))
    ready = integer(counts.get('matches_ready_for_model'))
    next6 = integer(counts.get('matches_next_6h'))
    next6_ready = integer(counts.get('matches_next_6h_ready'))
    next12 = integer(counts.get('matches_next_12h'))
    next12_ready = integer(counts.get('matches_next_12h_ready'))

    evaluated = rows_from(fallback)
    reason_counter = Counter()
    for row in evaluated:
        reason_counter.update(reasons(row))
    if not reason_counter and isinstance(detailed.get('reason_counts'), dict):
        reason_counter.update({str(k): integer(v) for k, v in detailed.get('reason_counts', {}).items()})

    providers = (((debug.get('provider_diagnostics') or {}).get('summary') or {}).get('providers') or {}) if isinstance(debug, dict) else {}
    odds_stats = (((providers.get('odds_api_io') or {}).get('stats') or {}).get('stats') or (providers.get('odds_api_io') or {}).get('stats') or {}) if isinstance(providers, dict) else {}
    accounts = odds_stats.get('accounts') if isinstance(odds_stats.get('accounts'), dict) else {}
    account2 = accounts.get('account2') if isinstance(accounts.get('account2'), dict) else {}

    blockers = []
    actions = []
    if total and with_odds < int(total * 0.80):
        blockers.append('line_coverage_below_80pct')
        actions.append('raise usable line coverage: account2 fallback, Bzzoiro/SportLogic secondary odds, same-side 2-book backfill')
    if total and with_context < int(total * 0.85):
        blockers.append('context_coverage_below_85pct')
        actions.append('raise context coverage: Bzzoiro near-window match, SStats deep context, weather only for top candidates')
    if next6 and next6_ready < int(next6 * 0.80):
        blockers.append('near_6h_model_ready_below_80pct')
        actions.append('prioritize next-6h inventory for odds/context before wider inventory')
    if integer(account2.get('offers_parsed')) <= 0:
        blockers.append('odds_api_io_account2_zero_offers')
        actions.append('enable account2 entitlement fallback/unfiltered retry diagnostics or replace Betfair/Sbobet set')
    if any('semantic_line_movement' in r for r, _ in reason_counter.most_common(20)):
        blockers.append('line_movement_top_blocker')
        actions.append('classify movement failures and store awaiting-movement candidates for next cron instead of losing them')
    if any('final_edge_below_min' in r or 'canonical_edge_below_min' in r for r in reason_counter):
        blockers.append('final_edge_explainability_gap')
        actions.append('expose displayed vs canonical vs post-recheck EV/edge in near-miss report')
    if any('proxy_default_xg_placeholder' in r for r in reason_counter):
        blockers.append('proxy_xg_placeholder_still_present')
        actions.append('verify proxy xG replacement order and safety-contract reason cleanup')

    score = 100
    score -= max(0, 80 - pct(with_odds, total)) * 0.35 if total else 10
    score -= max(0, 85 - pct(with_context, total)) * 0.25 if total else 10
    score -= max(0, 80 - pct(ready, total)) * 0.25 if total else 10
    score -= 8 if integer(account2.get('offers_parsed')) <= 0 else 0
    score -= min(15, sum(c for r, c in reason_counter.items() if 'semantic_line_movement' in r))
    score = round(max(0, min(100, score)), 1)

    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'ideal_score': score,
        'inventory': {
            'total': total,
            'with_lines': with_odds,
            'with_lines_pct': pct(with_odds, total),
            'with_context': with_context,
            'with_context_pct': pct(with_context, total),
            'ready': ready,
            'ready_pct': pct(ready, total),
            'next_6h_ready': next6_ready,
            'next_6h_total': next6,
            'next_6h_ready_pct': pct(next6_ready, next6),
            'next_12h_ready': next12_ready,
            'next_12h_total': next12,
            'next_12h_ready_pct': pct(next12_ready, next12),
        },
        'odds_api_io_account2': {
            'offers': integer(account2.get('offers_parsed')),
            'requests': integer(account2.get('odds_requests')),
            'http_statuses': account2.get('http_statuses') or [],
            'plan_restriction': bool(account2.get('plan_restriction')),
            'rate_limited': bool(account2.get('rate_limited')),
        },
        'bzzoiro_chain': {
            'overlap_bridge_offers': integer(bzz_bridge.get('bzzoiro_offer_rows')),
            'overlap_matches': integer(bzz_bridge.get('unique_overlap_match_count')),
            'merge_after_2plus_sources': integer(bzz_merge.get('after_2plus_sources')),
            'merge_after_2plus_books': integer(bzz_merge.get('after_2plus_books')),
        },
        'line_movement': line_diag,
        'proxy_xg_replacement': proxy,
        'top_reason_counts': dict(reason_counter.most_common(20)),
        'best_near_misses': best_near_misses(evaluated),
        'blockers': list(dict.fromkeys(blockers)),
        'recommended_actions': list(dict.fromkeys(actions)),
    }

    lines = [
        '🧭 HARIZON ideal runtime audit',
        f"• Ideal score: {score}/100",
        f"• Inventory: {total}; lines {with_odds}/{total} ({pct(with_odds,total)}%); context {with_context}/{total} ({pct(with_context,total)}%); ready {ready}/{total} ({pct(ready,total)}%)",
        f"• Near 6h ready: {next6_ready}/{next6} ({pct(next6_ready,next6)}%); near 12h ready: {next12_ready}/{next12} ({pct(next12_ready,next12)}%)",
        f"• odds-api.io account2: offers {integer(account2.get('offers_parsed'))}; req {integer(account2.get('odds_requests'))}; http {account2.get('http_statuses') or []}",
        f"• Bzzoiro bridge: offers {integer(bzz_bridge.get('bzzoiro_offer_rows'))}; overlap {integer(bzz_bridge.get('unique_overlap_match_count'))}; 2-source {integer(bzz_merge.get('after_2plus_sources'))}",
        '• Top blockers: ' + ', '.join(payload['blockers'][:8]) if payload['blockers'] else '• Top blockers: none',
        '• Next actions:',
    ]
    lines.extend(f"  - {item}" for item in payload['recommended_actions'][:10])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    OUT_TXT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
