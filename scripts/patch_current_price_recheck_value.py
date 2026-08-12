from __future__ import annotations

import re
from typing import Any

def _num(v: Any, default: float = 0.0) -> float:
    try: return float(str(v).replace(',', '.')) if v not in (None, '') else default
    except Exception: return default

def _prob_from_ev(price: float, ev_pct: float) -> float:
    return max(0.0, min(0.98, (1.0 + ev_pct / 100.0) / price)) if price > 1.0 else 0.0

def _selected_current(reason: str) -> tuple[float, float] | None:
    m = re.search(r'semantic_selected_price_not_current:([0-9.]+)/([0-9.]+)', str(reason))
    return (_num(m.group(1)), _num(m.group(2))) if m else None

def install(base: Any) -> dict[str, Any]:
    old = getattr(base, 'hard_reject_reasons', None)
    if not callable(old) or getattr(base, '_harizon_current_price_recheck', False):
        return {'status': 'already_installed' if getattr(base, '_harizon_current_price_recheck', False) else 'missing_hard_reject'}
    def hard_reject(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(old(candidate, metrics, sent_index) or [])
        ev = max(_num(metrics.get('canonical_ev_pct')), _num(metrics.get('ev_pct')), _num(candidate.get('ev_pct')))
        new_reasons: list[str] = []
        rechecked = False
        for reason in reasons:
            pair = _selected_current(str(reason))
            if not pair:
                new_reasons.append(reason); continue
            selected, current = pair
            prob = _prob_from_ev(selected, ev)
            current_ev = (prob * current - 1.0) * 100.0 if current > 1.0 else -999.0
            current_edge = (prob - 1.0 / current) * 100.0 if current > 1.0 else -999.0
            drift_pct = abs(selected - current) / max(current, 1e-9) * 100.0
            alive = current_ev >= 4.0 and current_edge >= 2.3
            metrics['current_price_recheck'] = {'selected_price': round(selected,4),'current_price': round(current,4),'model_probability_from_selected_ev': round(prob,5),'recalculated_ev_pct': round(current_ev,2),'recalculated_edge_pp': round(current_edge,2),'drift_pct': round(drift_pct,2),'status': 'value_still_valid_at_current_price' if alive else 'value_lost_after_current_price_recheck'}
            if alive and drift_pct <= 8.0:
                rechecked = True; continue
            new_reasons.append((f'current_price_recheck_value_alive_but_drift_too_high:{current_ev:.1f}/{current_edge:.1f}') if alive else (f'current_price_recheck_value_lost:{current_ev:.1f}/{current_edge:.1f}'))
        if rechecked: metrics.setdefault('repaired_reasons', []).append('semantic_selected_price_not_current_rechecked_value_alive')
        return list(dict.fromkeys(new_reasons))
    base.hard_reject_reasons = hard_reject; base._harizon_current_price_recheck = True
    return {'status': 'installed', 'publication_contract_relaxed': False}

__all__ = ['install']
