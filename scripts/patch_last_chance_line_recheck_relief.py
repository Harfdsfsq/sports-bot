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
    # Normalize separators because report/reject reasons mix spaces and underscores.
    r=str(reason or '').lower().replace('_',' ').replace('-',' ')
    hard=(
        'semantic line movement failed',
        'bad movement',
        'xg',
        'duplicate',
        'odds below',
        'odds above',
        'price integrity',
        'daily limit',
        'daily cap',
        'drift too high',
        'value lost',
        'family not allowed',
        'final ev below min',
    )
    return any(t in r for t in hard)


def _is_relievable_line_reason(reason: str) -> bool:
    r=str(reason or '').lower().replace('_',' ').replace('-',' ')
    return any(t in r for t in (
        'next regular run before kickoff',
        'no next regular run',
        'missing line recheck',
        'semantic line movement not confirmed',
        'unconfirmed final',
        'needs next cron line movement recheck',
    ))


def install(base: Any) -> dict[str, Any]:
    old=getattr(base,'hard_reject_reasons',None)
    if not callable(old) or getattr(base,'_harizon_last_chance_line_recheck_relief',False): return {'status':'already_installed' if getattr(base,'_harizon_last_chance_line_recheck_relief',False) else 'missing_hard_reject'}
    def wrapped(candidate:dict[str,Any], metrics:dict[str,Any], sent_index:dict[str,Any])->list[str]:
        reasons=list(old(candidate,metrics,sent_index) or []); _STATE['seen']+=1
        lower=[str(r).lower() for r in reasons]
        no_next=any('next regular run before kickoff' in r or 'no next regular run' in r or 'needs_next_cron_line_movement_recheck' in r for r in lower)
        missing=any(_is_relievable_line_reason(r) for r in reasons)
        floor=_floor(metrics)
        if floor: _STATE['floor_passed']+=1
        hard=any(_blocked_by_hard_guard(r) for r in reasons)
        if hard: _STATE['blocked_by_hard_guard']+=1
        # Keep richer diagnostics so the next artifact explains why relief did or did not apply.
        relief_eligible = bool(missing and floor and not hard)
        if len(_STATE['samples'])<30: _STATE['samples'].append({'home':candidate.get('home_team'),'away':candidate.get('away_team'),'floor':floor,'no_next':no_next,'missing':missing,'hard':hard,'relief_eligible':relief_eligible,'metrics':{'ev':max(_num(metrics.get('canonical_ev_pct')),_num(metrics.get('ev_pct'))),'edge':max(_num(metrics.get('canonical_edge_pp')),_num(metrics.get('edge_pp'))),'q':max(_num(metrics.get('reserve_quality_score')),_num(metrics.get('quality_score'))),'odds':_num(metrics.get('odds')),'books':max(_int(metrics.get('books_count')),_int(metrics.get('bookmaker_count')),2 if _int(metrics.get('price_confirmation_count'))>=2 else 0)},'reasons':reasons[:8]})
        # Last-chance relief should remove only soft lifecycle confirmation reasons.
        # Do NOT require no_next: artifacts showed floor=True/hard=False/missing=True
        # with semantic_line_movement_not_confirmed only, but no no_next flag, so the
        # previous condition never relieved safe last-chance candidates.
        if relief_eligible:
            _STATE['relieved']+=1; metrics.setdefault('repaired_reasons',[]).append('last_chance_line_recheck_relief_value_floor_passed')
            reasons=[r for r in reasons if not _is_relievable_line_reason(r)]
        _write(); return reasons
    base.hard_reject_reasons=wrapped; base._harizon_last_chance_line_recheck_relief=True; _write()
    return {'status':'installed','publication_contract_relaxed':False}

__all__=['install']
