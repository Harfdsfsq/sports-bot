from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
FALLBACK = EXPORT / 'latest-controlled-fallback-report.json'
QUEUE = EXPORT / 'latest-a-tier-targeted-enrichment-queue.json'
OUT = EXPORT / 'latest-fallback-provider-enrichment-targets.json'


def _load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('evaluated','candidates','checked_candidates','rejected_candidates','rows','items'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else default
    except Exception:
        return default


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(str(v).replace(',', '.'))) if v not in (None, '') else 0
    except Exception:
        return 0


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r'[,|;/+]+', value) if x.strip()]
    return []


def _unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    cand = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else cand.get('metrics') if isinstance(cand.get('metrics'), dict) else {}
    reasons = row.get('reject_reasons') or row.get('reasons') or row.get('hard_reject_reasons') or cand.get('reject_reasons') or cand.get('reasons') or []
    return cand, metrics, [str(x) for x in (reasons if isinstance(reasons, list) else [reasons]) if str(x).strip()]


def _metric(c: dict[str, Any], m: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if m.get(key) not in (None, ''):
            return _num(m.get(key))
        if c.get(key) not in (None, ''):
            return _num(c.get(key))
    return 0.0


def _count(c: dict[str, Any], m: dict[str, Any], *keys: str) -> int:
    vals: list[int] = []
    for key in keys:
        vals.append(_int(m.get(key)))
        vals.append(_int(c.get(key)))
    return max(vals or [0])


def _base_item(c: dict[str, Any], m: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    ev = _metric(c, m, 'canonical_ev_pct', 'ev_pct')
    edge = _metric(c, m, 'canonical_edge_pp', 'edge_pp')
    odds = _metric(c, m, 'odds', 'selected_odds')
    quality = _metric(c, m, 'quality_score', 'quality')
    confidence = _metric(c, m, 'confidence')
    odds_sources = _count(c, m, 'odds_sources_count', 'line_sources_count', 'independent_odds_sources_count')
    books = _count(c, m, 'books_count', 'bookmaker_count', 'bookmakers_count', 'price_confirmation_sources_count')
    ctx = _count(c, m, 'confirmation_sources_count', 'context_sources_count', 'sources_count')
    conf_sources = sorted(set(_split(m.get('confirmation_sources')) + _split(c.get('confirmation_sources')) + _split(m.get('context_sources')) + _split(c.get('context_sources'))))
    return {
        'match_key': c.get('match_key') or c.get('canonical_match_id'),
        'home_team': c.get('home_team') or c.get('home'),
        'away_team': c.get('away_team') or c.get('away'),
        'kickoff': c.get('commence_time') or c.get('kickoff_utc') or c.get('start_time') or c.get('kickoff'),
        'league': c.get('league_name') or c.get('league') or c.get('competition'),
        'family': c.get('family') or c.get('market_family'),
        'selection': c.get('selection'),
        'point': c.get('point'),
        'odds': odds,
        'ev_pct': ev,
        'edge_pp': edge,
        'quality': quality,
        'confidence': confidence,
        'odds_sources': odds_sources,
        'books': books,
        'context_sources': ctx,
        'confirmation_sources': conf_sources,
        'reject_reasons': reasons[:8],
        'priority': round(ev * 3.0 + edge * 6.0 + quality * 0.25 + confidence * 0.15 + books * 4.0 + odds_sources * 5.0 - ctx * 3.0, 2),
    }


def _should_target(item: dict[str, Any]) -> bool:
    reasons = ' '.join(item.get('reject_reasons') or []).lower()
    if item['ev_pct'] < 1.5 and item['edge_pp'] < 0.7:
        return False
    if item['odds'] and not (1.55 <= item['odds'] <= 3.35):
        return False
    return item['context_sources'] < 2 or item['odds_sources'] < 2 or 'missing' in reasons or 'подтверж' in reasons or 'price_integrity' in reasons


def main() -> int:
    fallback = _load(FALLBACK, {})
    queue = _load(QUEUE, {})
    evaluated = _rows(fallback)
    bzz: dict[str, dict[str, Any]] = {}
    ctx: dict[str, dict[str, Any]] = {}
    recheck: dict[str, dict[str, Any]] = {}
    price_review: dict[str, dict[str, Any]] = {}
    for row in evaluated:
        cand, metrics, reasons = _unwrap(row)
        item = _base_item(cand, metrics, reasons)
        if not item.get('match_key') and not (item.get('home_team') and item.get('away_team')):
            continue
        if not _should_target(item):
            continue
        key = str(item.get('match_key') or f"{item.get('home_team')}|{item.get('away_team')}|{item.get('kickoff')}")
        reason_text = ' '.join(reasons).lower()
        if item['odds_sources'] < 2 or item['books'] >= 2:
            x = dict(item); x['recommended_provider'] = 'bzzoiro'; x['recommended_action'] = 'confirm_independent_odds_or_same_side_price'; bzz[key] = x
        if item['context_sources'] < 2:
            x = dict(item); x['recommended_provider'] = 'sstats,bzzoiro,football_data,thesportsdb,openfootball'; x['recommended_action'] = 'add_independent_context_confirmation'; ctx[key] = x
        if 'current price recheck' in reason_text or 'line recheck' in reason_text or 'next regular run' in reason_text:
            x = dict(item); x['recommended_provider'] = 'odds_api_io,bzzoiro'; x['recommended_action'] = 'refresh_current_price_and_line_movement'; recheck[key] = x
        if 'price integrity' in reason_text and item['ev_pct'] >= 6.0 and item['edge_pp'] >= 2.5:
            x = dict(item); x['recommended_provider'] = 'odds_api_io,bzzoiro'; x['recommended_action'] = 'verify_outlier_against_same_side_snapshot'; price_review[key] = x
    def top(d: dict[str, dict[str, Any]], n: int) -> list[dict[str, Any]]:
        return sorted(d.values(), key=lambda x: -_num(x.get('priority')))[:n]
    existing_bzz = queue.get('bzzoiro_odds_targets') if isinstance(queue.get('bzzoiro_odds_targets'), list) else []
    existing_ctx = queue.get('context_projection_targets') if isinstance(queue.get('context_projection_targets'), list) else []
    existing_fast = queue.get('high_value_recheck_targets') if isinstance(queue.get('high_value_recheck_targets'), list) else []
    merged_bzz = top({str(x.get('match_key') or i): x for i, x in enumerate(existing_bzz) if isinstance(x, dict)} | bzz, 120)
    merged_ctx = top({str(x.get('match_key') or i): x for i, x in enumerate(existing_ctx) if isinstance(x, dict)} | ctx, 120)
    merged_fast = top({str(x.get('match_key') or i): x for i, x in enumerate(existing_fast) if isinstance(x, dict)} | recheck, 40)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'fallback_evaluated': len(evaluated),
        'bzzoiro_odds_targets_added': len(bzz),
        'context_projection_targets_added': len(ctx),
        'high_value_recheck_targets_added': len(recheck),
        'price_integrity_review_targets_added': len(price_review),
        'bzzoiro_odds_targets': top(bzz, 80),
        'context_projection_targets': top(ctx, 80),
        'high_value_recheck_targets': top(recheck, 40),
        'price_integrity_review_targets': top(price_review, 30),
        'publication_contract_relaxed': False,
    }
    queue_out = dict(queue) if isinstance(queue, dict) else {}
    queue_out['status'] = 'ok'
    queue_out['created_at_utc'] = payload['created_at_utc']
    queue_out['bzzoiro_odds_targets'] = merged_bzz
    queue_out['context_projection_targets'] = merged_ctx
    queue_out['high_value_recheck_targets'] = merged_fast
    queue_out['price_integrity_review_targets'] = top(price_review, 30)
    queue_out['summary'] = {
        **(queue_out.get('summary') if isinstance(queue_out.get('summary'), dict) else {}),
        'bzzoiro_odds_target_count': len(merged_bzz),
        'context_projection_target_count': len(merged_ctx),
        'high_value_recheck_target_count': len(merged_fast),
        'price_integrity_review_target_count': len(queue_out['price_integrity_review_targets']),
        'fallback_targets_added': len(bzz) + len(ctx) + len(recheck) + len(price_review),
    }
    _write(OUT, payload)
    _write(QUEUE, queue_out)
    _write(EXPORT / 'latest-high-value-recheck-queue.json', {'created_at_utc': payload['created_at_utc'], 'items': merged_fast})
    print(json.dumps(queue_out['summary'], ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
