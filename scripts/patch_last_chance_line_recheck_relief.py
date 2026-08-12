from __future__ import annotations

"""Controlled last-chance line recheck relief.

Only removes missing/unconfirmed recheck reasons when there is no later regular
run before kickoff and the candidate already clears strict B-tier value floors.
It does not bypass bad movement, duplicate, xG conflict, odds min, price drift,
or daily cap guards.
"""

from typing import Any


def _num(v: Any, d: float = 0.0) -> float:
    try: return float(str(v).replace(',', '.')) if v not in (None, '') else d
    except Exception: return d


def _int(v: Any, d: int = 0) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)): return len(v)
        return int(float(v)) if v not in (None, '') else d
    except Exception: return d


def _floor(metrics: dict[str, Any]) -> bool:
    ev = max(_num(metrics.get('canonical_ev_pct')), _num(metrics.get('ev_pct')))
    edge = max(_num(metrics.get('canonical_edge_pp')), _num(metrics.get('edge_pp')))
    q = max(_num(metrics.get('reserve_quality_score')), _num(metrics.get('quality_score')))
    odds = _num(metrics.get('odds'))
    books = max(_int(metrics.get('books_count')), _int(metrics.get('bookmaker_count')))
    ctx = max(_int(metrics.get('context_sources_count')), _int(metrics.get('confirmation_sources_count')))
    return ev >= 5.0 and edge >= 2.7 and q >= 70.0 and books >= 2 and ctx >= 1 and 1.70 <= odds <= 2.85


def _blocked_by_hard_guard(reason: str) -> bool:
    r = reason.lower()
    hard_tokens = ('semantic line movement failed','bad_movement','xg','duplicate','odds below','price integrity','daily limit','drift_too_high','value_lost','family not allowed')
    return any(t in r for t in hard_tokens)


def install(base: Any) -> dict[str, Any]:
    old = getattr(base, 'hard_reject_reasons', None)
    if not callable(old) or getattr(base, '_harizon_last_chance_line_recheck_relief', False):
        return {'status': 'already_installed' if getattr(base, '_harizon_last_chance_line_recheck_relief', False) else 'missing_hard_reject'}
    def wrapped(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(old(candidate, metrics, sent_index) or [])
        if not reasons or not _floor(metrics): return reasons
        lower = [str(r).lower() for r in reasons]
        no_next = any('next regular run before kickoff' in r or 'no next regular run' in r for r in lower)
        missing = any('missing line recheck' in r or 'semantic line movement not confirmed' in r or 'unconfirmed final' in r for r in lower)
        has_hard = any(_blocked_by_hard_guard(r) for r in lower)
        if no_next and missing and not has_hard:
            metrics.setdefault('repaired_reasons', []).append('last_chance_line_recheck_relief_value_floor_passed')
            return [r for r in reasons if not any(t in str(r).lower() for t in ('next regular run before kickoff','missing line recheck','semantic line movement not confirmed','unconfirmed final'))]
        return reasons
    base.hard_reject_reasons = wrapped
    base._harizon_last_chance_line_recheck_relief = True
    return {'status':'installed','publication_contract_relaxed':False}

__all__ = ['install']
