from __future__ import annotations

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-controlled-fallback-report.json'
FILTER_PATH = EXPORT_DIR / 'latest-controlled-fallback-value-filter.json'
_INSTALLED = False


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _metrics(row: dict[str, Any]) -> dict[str, float]:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    odds = _as_float(metrics.get('odds') or row.get('odds'), 0.0)
    adjusted = _as_float(metrics.get('adjusted_probability') or row.get('adjusted_probability'), 0.0)
    implied = _as_float(metrics.get('selected_implied_probability'), 0.0)
    if implied <= 0.0 and odds > 1.0:
        implied = 1.0 / odds
    ev = _as_float(metrics.get('canonical_ev_pct'), 9999.0)
    edge = _as_float(metrics.get('canonical_edge_pp'), 9999.0)
    if ev == 9999.0 and odds > 1.0 and adjusted > 0.0:
        ev = (adjusted * odds - 1.0) * 100.0
    if edge == 9999.0 and adjusted > 0.0 and implied > 0.0:
        edge = (adjusted - implied) * 100.0
    return {'canonical_ev_pct': ev, 'canonical_edge_pp': edge}


def filter_report() -> dict[str, Any]:
    payload = _load(REPORT_PATH)
    if not payload or not isinstance(payload.get('evaluated'), list):
        result = {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'no_report_or_no_evaluated'}
        _write(FILTER_PATH, result)
        return result
    min_ev = _as_float(os.getenv('CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EV_PCT'), 0.0)
    min_edge = _as_float(os.getenv('CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EDGE_PP'), 0.0)
    evaluated = [x for x in payload.get('evaluated') if isinstance(x, dict)]
    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for row in evaluated:
        m = _metrics(row)
        if m['canonical_ev_pct'] >= min_ev and m['canonical_edge_pp'] >= min_edge:
            kept.append(row)
        else:
            copy = dict(row)
            copy['candidate_value_filter'] = {'reason': 'negative_canonical_value', 'canonical_ev_pct': round(m['canonical_ev_pct'], 4), 'canonical_edge_pp': round(m['canonical_edge_pp'], 4), 'min_ev_pct': min_ev, 'min_edge_pp': min_edge}
            discarded.append(copy)
    payload['evaluated'] = kept
    payload['candidates_seen_before_value_filter'] = int(payload.get('candidates_seen') or len(evaluated))
    payload['candidates_seen'] = len(kept)
    payload['discarded_negative_value_candidates'] = discarded
    payload['candidate_value_post_filter'] = {'enabled': True, 'created_at_utc': datetime.now(UTC).isoformat(), 'input_evaluated': len(evaluated), 'kept': len(kept), 'discarded_negative_value': len(discarded), 'min_ev_pct': min_ev, 'min_edge_pp': min_edge}
    if not kept and not bool(payload.get('published')):
        payload['status'] = 'no_positive_canonical_value_candidates'
        payload['main_reason'] = 'no_positive_canonical_value_after_selected_price_recalc'
        payload['main_reason_ru'] = 'нет кандидатов с положительной canonical value после пересчёта по выбранному коэффициенту'
    _write(REPORT_PATH, payload)
    result = {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'filtered', 'input_evaluated': len(evaluated), 'kept': len(kept), 'discarded_negative_value': len(discarded), 'report_status': payload.get('status')}
    _write(FILTER_PATH, result)
    return result


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _INSTALLED = True
    atexit.register(filter_report)
    result = {'status': 'installed', 'hook': 'atexit', 'path': str(REPORT_PATH)}
    _write(FILTER_PATH, {'created_at_utc': datetime.now(UTC).isoformat(), **result})
    return result
