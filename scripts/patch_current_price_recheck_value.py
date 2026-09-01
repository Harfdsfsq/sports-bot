from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-current-price-recheck-value.json')
PATCH_VERSION = 'v3-positive-reason-normalizer-after-semantic'


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else default
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(str(raw).replace(',', '.')) if raw not in (None, '') else default
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _prob_from_ev(price: float, ev_pct: float) -> float:
    return max(0.0, min(0.98, (1.0 + ev_pct / 100.0) / price)) if price > 1.0 else 0.0


def _selected_current(reason: str) -> tuple[float, float] | None:
    m = re.search(r'semantic_selected_price_not_current:([0-9.]+)/([0-9.]+)', str(reason))
    return (_num(m.group(1)), _num(m.group(2))) if m else None


def _existing_recheck_result(reason: str) -> tuple[float, float] | None:
    m = re.search(r'current[_ ]price[_ ]recheck[_ ]value[_ ](?:lost|alive[^:]*):([+-]?[0-9.]+)/([+-]?[0-9.]+)', str(reason), flags=re.I)
    return (_num(m.group(1)), _num(m.group(2))) if m else None


def _current_price_bounds_ok(current: float) -> bool:
    min_odds = _env_float('ODDS_MIN', _env_float('CONTROLLED_FALLBACK_MIN_ODDS', 1.45))
    max_odds = _env_float('ODDS_MAX', _env_float('CONTROLLED_FALLBACK_MAX_ODDS', 3.25))
    return min_odds <= current <= max_odds


def _is_our_wrapper(fn: Any) -> bool:
    return bool(getattr(fn, '_harizon_current_price_recheck_wrapper', False))


def _base_for_reinstall(base: Any, old: Any) -> Any:
    # Earlier runs installed this patch before semantic current-price guard, which
    # means it never saw semantic_selected_price_not_current reasons.  Allow a
    # reinstall when semantic guard is now the active wrapper; otherwise keep the
    # existing wrapper to avoid recursive double wrapping.
    previous = getattr(base, '_harizon_current_price_recheck_wrapped_fn', None)
    if _is_our_wrapper(old) and callable(previous):
        return previous
    return old


def install(base: Any) -> dict[str, Any]:
    old = getattr(base, 'hard_reject_reasons', None)
    if not callable(old):
        return {'status': 'missing_hard_reject'}
    if getattr(base, '_harizon_current_price_recheck_version', '') == PATCH_VERSION and _is_our_wrapper(old):
        return {'status': 'already_installed', 'version': PATCH_VERSION}
    wrapped_target = _base_for_reinstall(base, old)
    if not callable(wrapped_target):
        return {'status': 'missing_wrapped_target'}

    def hard_reject(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(wrapped_target(candidate, metrics, sent_index) or [])
        ev = max(_num(metrics.get('canonical_ev_pct')), _num(metrics.get('ev_pct')), _num(candidate.get('ev_pct')))
        min_ev = _env_float('CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EV_PCT', 3.0)
        min_edge = _env_float('CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EDGE_PP', 1.5)
        hard_drift = _env_float('CONTROLLED_FALLBACK_CURRENT_PRICE_HARD_DRIFT_PCT', 35.0)
        allow_alive_high_drift = _env_bool('CONTROLLED_FALLBACK_ALLOW_VALUE_ALIVE_HIGH_DRIFT', True)
        new_reasons: list[str] = []
        rechecked = False
        samples: list[dict[str, Any]] = []
        for reason in reasons:
            existing = _existing_recheck_result(str(reason))
            if existing is not None:
                current_ev, current_edge = existing
                alive = current_ev >= min_ev and current_edge >= min_edge
                sample = {
                    'source_reason': str(reason),
                    'recalculated_ev_pct': round(current_ev, 2),
                    'recalculated_edge_pp': round(current_edge, 2),
                    'min_ev_pct': min_ev,
                    'min_edge_pp': min_edge,
                    'status': 'existing_recheck_value_alive' if alive else 'existing_recheck_value_lost',
                }
                samples.append(sample)
                if alive:
                    metrics['current_price_recheck'] = sample
                    metrics.setdefault('repaired_reasons', []).append('positive_current_price_recheck_reason_normalized')
                    rechecked = True
                    continue
                new_reasons.append(f'current_price_recheck_value_lost:{current_ev:.1f}/{current_edge:.1f}')
                continue

            pair = _selected_current(str(reason))
            if not pair:
                new_reasons.append(reason)
                continue
            selected, current = pair
            prob = _prob_from_ev(selected, ev)
            current_ev = (prob * current - 1.0) * 100.0 if current > 1.0 else -999.0
            current_edge = (prob - 1.0 / current) * 100.0 if current > 1.0 else -999.0
            drift_pct = abs(selected - current) / max(current, 1e-9) * 100.0
            alive = current_ev >= min_ev and current_edge >= min_edge and _current_price_bounds_ok(current)
            sample = {
                'selected_price': round(selected, 4),
                'current_price': round(current, 4),
                'model_probability_from_selected_ev': round(prob, 5),
                'recalculated_ev_pct': round(current_ev, 2),
                'recalculated_edge_pp': round(current_edge, 2),
                'drift_pct': round(drift_pct, 2),
                'min_ev_pct': min_ev,
                'min_edge_pp': min_edge,
                'hard_drift_pct': hard_drift,
                'current_price_bounds_ok': _current_price_bounds_ok(current),
            }
            if alive and (drift_pct <= hard_drift or allow_alive_high_drift):
                sample['status'] = 'value_alive_at_current_price_high_drift_warning' if drift_pct > hard_drift else 'value_alive_at_current_price'
                metrics['current_price_recheck'] = sample
                metrics.setdefault('current_price_recheck_warnings', [])
                if drift_pct > hard_drift:
                    metrics['current_price_recheck_warnings'].append(f'high_drift:{drift_pct:.1f}%')
                rechecked = True
                samples.append(sample)
                continue
            sample['status'] = 'value_lost_after_current_price_recheck' if not alive else 'value_alive_but_extreme_drift_blocked'
            metrics['current_price_recheck'] = sample
            samples.append(sample)
            if alive:
                new_reasons.append(f'current_price_recheck_value_alive_but_extreme_drift:{current_ev:.1f}/{current_edge:.1f}')
            else:
                new_reasons.append(f'current_price_recheck_value_lost:{current_ev:.1f}/{current_edge:.1f}')
        if rechecked:
            metrics.setdefault('repaired_reasons', []).append('semantic_selected_price_not_current_rechecked_value_alive')
        _write({
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'ok',
            'version': PATCH_VERSION,
            'match_key': candidate.get('match_key'),
            'home_team': candidate.get('home_team'),
            'away_team': candidate.get('away_team'),
            'family': candidate.get('family'),
            'selection': candidate.get('selection'),
            'point': candidate.get('point'),
            'samples': samples,
            'hard_reasons_after': [str(x) for x in new_reasons],
            'publication_contract_relaxed': False,
            'note': 'Installed after semantic guard; positive current-price EV/edge is never emitted as value_lost.',
        })
        return list(dict.fromkeys(new_reasons))

    hard_reject._harizon_current_price_recheck_wrapper = True  # type: ignore[attr-defined]
    base.hard_reject_reasons = hard_reject
    base._harizon_current_price_recheck = True
    base._harizon_current_price_recheck_version = PATCH_VERSION
    base._harizon_current_price_recheck_wrapped_fn = wrapped_target
    return {'status': 'installed', 'version': PATCH_VERSION, 'publication_contract_relaxed': False}


__all__ = ['install']
