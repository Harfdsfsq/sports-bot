from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-last-chance-line-recheck-relief.json')
_STATE = {'seen':0,'floor_passed':0,'blocked_by_hard_guard':0,'relieved':0,'samples':[]}


def _write() -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({'created_at_utc': datetime.now(UTC).isoformat(), **_STATE, 'publication_contract_relaxed': False}, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    except Exception: pass


def _num(v: Any, d: float = 0.0) -> float:
    try: return float(str(v).replace(',', '.')) if v not in (None, '') else d
    except Exception: return d


def _int(v: Any, d: int = 0) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)): return len(v)
        return int(float(v)) if v not in (None, '') else d
    except Exception: return d


def _floor(metrics: dict[str, Any]) -> bool:
    ev=max(_num(metrics.get('canonical_ev_pct')),_num(metrics.get('ev_pct'))); edge=max(_num(metrics.get('canonical_edge_pp')),_num(metrics.get('edge_pp'))); q=max(_num(metrics.get('reserve_quality_score')),_num(metrics.get('quality_score'))); odds=_num(metrics.get('odds')); books=max(_int(metrics.get('books_count')),_int(metrics.get('bookmaker_count')),2 if _int(metrics.get('price_confirmation_count'))>=2 else 0); ctx=max(_int(metrics.get('context_sources_count')),_int(metrics.get('confirmation_sources_count')),1 if metrics.get('context') else 0)
    return ev >= 5.0 and edge >= 2.7 and q >= 70.0 and books >= 2 and ctx >= 1 and 1.70 <= odds <= 2.85


def _blocked_by_hard_guard(reason: str) -> bool:
    r=reason.lower(); hard=('semantic line movement failed','bad_movement','xg','duplicate','odds below','price integrity','daily limit','drift_too_high','value_lost','family not allowed')
    return any(t in r for t in hard)


def install(base: Any) -> dict[str, Any]:
    old=getattr(base,'hard_reject_reasons',None)
    if not callable(old) or getattr(base,'_harizon_last_chance_line_recheck_relief',False): return {'status':'already_installed' if getattr(base,'_harizon_last_chance_line_recheck_relief',False) else 'missing_hard_reject'}
    def wrapped(candidate:dict[str,Any], metrics:dict[str,Any], sent_index:dict[str,Any])->list[str]:
        reasons=list(old(candidate,metrics,sent_index) or []); _STATE['seen']+=1
        lower=[str(r).lower() for r in reasons]
        no_next=any('next regular run before kickoff' in r or 'no next regular run' in r for r in lower)
        missing=any('missing line recheck' in r or 'semantic line movement not confirmed' in r or 'unconfirmed final' in r for r in lower)
        floor=_floor(metrics)
        if floor: _STATE['floor_passed']+=1
        hard=any(_blocked_by_hard_guard(r) for r in lower)
        if hard: _STATE['blocked_by_hard_guard']+=1
        if len(_STATE['samples'])<20: _STATE['samples'].append({'home':candidate.get('home_team'),'away':candidate.get('away_team'),'floor':floor,'no_next':no_next,'missing':missing,'hard':hard,'reasons':reasons[:5]})
        if no_next and missing and floor and not hard:
            _STATE['relieved']+=1; metrics.setdefault('repaired_reasons',[]).append('last_chance_line_recheck_relief_value_floor_passed')
            reasons=[r for r in reasons if not any(t in str(r).lower() for t in ('next regular run before kickoff','missing line recheck','semantic line movement not confirmed','unconfirmed final'))]
        _write(); return reasons
    base.hard_reject_reasons=wrapped; base._harizon_last_chance_line_recheck_relief=True; _write()
    return {'status':'installed','publication_contract_relaxed':False}

__all__=['install']
