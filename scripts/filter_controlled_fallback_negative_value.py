from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-controlled-fallback-report.json'
FILTER_PATH = EXPORT_DIR / 'latest-controlled-fallback-value-filter.json'


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def canonical_metrics(row: dict[str, Any]) -> dict[str, float]:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    odds = as_float(metrics.get('odds') or row.get('odds'), 0.0)
    adjusted = as_float(metrics.get('adjusted_probability') or row.get('adjusted_probability'), 0.0)
    implied = as_float(metrics.get('selected_implied_probability'), 0.0)
    if implied <= 0.0 and odds > 1.0:
        implied = 1.0 / odds
    ev = as_float(metrics.get('canonical_ev_pct'), 9999.0)
    edge = as_float(metrics.get('canonical_edge_pp'), 9999.0)
    if ev == 9999.0 and odds > 1.0 and adjusted > 0.0:
        ev = (adjusted * odds - 1.0) * 100.0
    if edge == 9999.0 and adjusted > 0.0 and implied > 0.0:
        edge = (adjusted - implied) * 100.0
    return {'canonical_ev_pct': ev, 'canonical_edge_pp': edge, 'odds': odds, 'adjusted_probability': adjusted, 'selected_implied_probability': implied}


def main() -> int:
    payload = load_json(REPORT_PATH)
    if not payload:
        write_json(FILTER_PATH, {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'missing_report'})
        return 0

    min_ev = as_float(os.getenv('CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EV_PCT'), 0.0)
    min_edge = as_float(os.getenv('CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EDGE_PP'), 0.0)
    evaluated = payload.get('evaluated') if isinstance(payload.get('evaluated'), list) else []
    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        m = canonical_metrics(row)
        if m['canonical_ev_pct'] >= min_ev and m['canonical_edge_pp'] >= min_edge:
            kept.append(row)
        else:
            item = dict(row)
            item['candidate_value_filter'] = {'reason': 'negative_canonical_value', 'canonical_ev_pct': round(m['canonical_ev_pct'], 4), 'canonical_edge_pp': round(m['canonical_edge_pp'], 4), 'min_ev_pct': min_ev, 'min_edge_pp': min_edge}
            discarded.append(item)

    payload['evaluated'] = kept
    payload['candidates_seen_before_value_filter'] = int(payload.get('candidates_seen') or len(evaluated))
    payload['candidates_seen'] = len(kept)
    payload['discarded_negative_value_candidates'] = discarded
    payload['candidate_value_post_filter'] = {'enabled': True, 'created_at_utc': datetime.now(UTC).isoformat(), 'input_evaluated': len(evaluated), 'kept': len(kept), 'discarded_negative_value': len(discarded), 'min_ev_pct': min_ev, 'min_edge_pp': min_edge}
    if not kept and not bool(payload.get('published')):
        payload['status'] = 'no_positive_canonical_value_candidates'
        payload['main_reason'] = 'no_positive_canonical_value_after_selected_price_recalc'
        payload['main_reason_ru'] = 'нет кандидатов с положительной canonical value после пересчёта по выбранному коэффициенту'
        payload['no_pick_report_sent'] = True

    write_json(REPORT_PATH, payload)
    write_json(FILTER_PATH, {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'filtered', 'input_evaluated': len(evaluated), 'kept': len(kept), 'discarded_negative_value': len(discarded), 'report_status': payload.get('status'), 'discarded_sample': [{'match_key': item.get('match_key'), 'selection': item.get('selection'), 'ev': (item.get('candidate_value_filter') or {}).get('canonical_ev_pct'), 'edge': (item.get('candidate_value_filter') or {}).get('canonical_edge_pp')} for item in discarded[:12]]})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
