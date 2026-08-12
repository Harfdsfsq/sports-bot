from __future__ import annotations

import json, os, re
from pathlib import Path
from typing import Any
EXPORT=Path('.data/exports')

def _load(path:str|Path,default:Any=None)->Any:
    try: return json.loads(Path(path).read_text(encoding='utf-8',errors='replace'))
    except Exception: return {} if default is None else default

def _int(v:Any)->int:
    try:
        if isinstance(v,(list,tuple,set,dict)): return len(v)
        return int(float(v))
    except Exception: return 0

def _num(v:Any,d:float=0.0)->float:
    try: return float(str(v).replace(',','.')) if v not in (None,'') else d
    except Exception: return d

def _disabled_sportlogic_env()->bool:
    return any(str(os.getenv(n) or '').lower() in {'0','false','no','off'} for n in ('DAY_INVENTORY_ENABLE_SPORTLOGIC','ENABLE_SPORTLOGIC','SPORTLOGIC_ENABLED','SPORTLOGIC_CONTROLLED_ODDS_ENABLED'))

def _reserve_quality_from_metrics(m:dict[str,Any])->float:
    direct=_num(m.get('reserve_quality_score') or m.get('quality_score'),-1)
    if direct>0: return direct
    ev=max(_num(m.get('canonical_ev_pct')),_num(m.get('ev_pct'))); edge=max(_num(m.get('canonical_edge_pp')),_num(m.get('edge_pp'))); odds=_num(m.get('odds')); books=max(_int(m.get('books_count')),_int(m.get('bookmaker_count')),2 if _int(m.get('confirmation_sources_count'))>=2 else 0); conf=max(_int(m.get('confirmation_sources_count')),_int(m.get('context_sources_count')))
    return round(max(0,min(100,38+min(18,ev*1.45)+min(16,edge*3)+min(10,books*3)+min(10,conf*1.5)+(4 if 1.75<=odds<=2.55 else (-8 if odds<1.70 or odds>2.90 else 0)))),1)

def patch_payload_quality(payload:dict[str,Any])->dict[str,Any]:
    samples=payload.get('samples') if isinstance(payload.get('samples'),dict) else {}; ev=samples.get('fallback_evaluated') if isinstance(samples.get('fallback_evaluated'),list) else []; patched=0
    for row in ev:
        if not isinstance(row,dict): continue
        m=row.get('metrics') if isinstance(row.get('metrics'),dict) else {}; q=_reserve_quality_from_metrics(m)
        if q>0: m['reserve_quality_score']=q; m['quality_score']=m.get('quality_score') or q; row['reserve_quality_score']=q; patched+=1
    payload.setdefault('diagnostic_repairs',{})['reserve_quality_samples_patched']=patched; return payload

def patch_payload(payload:dict[str,Any])->dict[str,Any]:
    if _disabled_sportlogic_env(): payload.setdefault('api',{})['sportlogic']={'enabled':False,'requests':0,'fixtures':0,'odds_requests':0,'matched':0,'offers':0,'errors':0,'diagnosis':'disabled_by_env'}
    q=_load(EXPORT/'latest-a-tier-targeted-enrichment-queue.json',{}); b=_load(EXPORT/'latest-bzzoiro-targeted-odds-confirmation.json',{}); trace=_load(EXPORT/'latest-bzzoiro-report-source-trace.json',{}); detail=_load(EXPORT/'latest-bzzoiro-targeted-odds-detail.json',{}); relief=_load(EXPORT/'latest-last-chance-line-recheck-relief.json',{})
    if isinstance(q,dict): payload.setdefault('a_tier_enrichment',{}).update(q.get('summary') or {})
    if isinstance(b,dict): payload.setdefault('a_tier_enrichment',{})['bzzoiro_targeted']={k:b.get(k) for k in ('targets','bzzoiro_events_seen','matched_events','offers','two_source_promoted','diagnosis','odds_detail')}
    if isinstance(trace,dict): payload.setdefault('a_tier_enrichment',{})['bzzoiro_trace']=trace
    if isinstance(detail,dict): payload.setdefault('a_tier_enrichment',{})['bzzoiro_detail']=detail
    if isinstance(relief,dict): payload.setdefault('line_guard',{})['last_chance_relief']=relief
    return payload

def patch_text_quality(text:str,payload:dict[str,Any])->str:
    ev=[x for x in ((payload.get('samples') or {}).get('fallback_evaluated') or []) if isinstance(x,dict)] if isinstance(payload.get('samples'),dict) else []
    for idx,row in enumerate(ev[:6],1):
        q=_reserve_quality_from_metrics(row.get('metrics') if isinstance(row.get('metrics'),dict) else {})
        if q>0: text=re.sub(rf'(^\s*{idx}\. .*? \| q )0\.0\b',rf'\g<1>{q:.1f} reserve',text,count=1,flags=re.MULTILINE)
    return text

def patch_runtime_lines(text:str)->str:
    if _disabled_sportlogic_env(): text=re.sub(r'^• sportlogic:.*$','• sportlogic: enabled False, req 0, odds req 0, matched 0, offers 0, err 0',text,count=1,flags=re.MULTILINE)
    trace=_load(EXPORT/'latest-bzzoiro-report-source-trace.json',{})
    if isinstance(trace,dict):
        persisted=_int(trace.get('persisted_event_rows')); diag=trace.get('diagnosis') or 'n/a'
        text=re.sub(r'^(• bzzoiro: req \d+, ctx \d+, events )(\d+)(?: \([^)]*\))?(, secondary offers .*)$',rf'\g<1>\2 (aggregate; persisted rows {persisted}; trace {diag})\3',text,count=1,flags=re.MULTILINE)
    return text

def patch_a_tier_summary(text:str)->str:
    q=_load(EXPORT/'latest-a-tier-targeted-enrichment-queue.json',{}); s=q.get('summary') if isinstance(q,dict) and isinstance(q.get('summary'),dict) else {}; b=_load(EXPORT/'latest-bzzoiro-targeted-odds-confirmation.json',{}); trace=_load(EXPORT/'latest-bzzoiro-report-source-trace.json',{}); detail=_load(EXPORT/'latest-bzzoiro-targeted-odds-detail.json',{}); relief=_load(EXPORT/'latest-last-chance-line-recheck-relief.json',{})
    if s and 'A-tier enrichment queue' not in text:
        text=text.replace('🧪 Воронка кандидатов',f"• A-tier enrichment queue: Bzzoiro odds targets {_int(s.get('bzzoiro_odds_target_count'))}; context projection targets {_int(s.get('context_projection_target_count'))}; high-value recheck {_int(s.get('high_value_recheck_target_count'))}.\n🧪 Воронка кандидатов",1)
    if isinstance(b,dict) and 'Bzzoiro targeted odds' not in text:
        text=text.replace('📡 Core API',f"• Bzzoiro targeted odds: targets {_int(b.get('targets'))}; events {_int(b.get('bzzoiro_events_seen'))}; matched {_int(b.get('matched_events'))}; offers {_int(b.get('offers'))}; 2-source promoted {_int(b.get('two_source_promoted'))}; diag {b.get('diagnosis') or 'n/a'}.\n📡 Core API",1)
    if isinstance(detail,dict) and 'Bzzoiro odds detail' not in text:
        text=text.replace('📡 Core API',f"• Bzzoiro odds detail: targets {_int(detail.get('targets'))}; requests {_int(detail.get('requests'))}; events_with_offers {_int(detail.get('events_with_offers'))}; offers {_int(detail.get('offers'))}; diag {detail.get('diagnosis') or 'n/a'}.\n📡 Core API",1)
    if isinstance(trace,dict) and 'Bzzoiro source trace' not in text:
        text=text.replace('📡 Core API',f"• Bzzoiro source trace: aggregate events {_int(trace.get('source_stats_events_fetched'))}; persisted rows {_int(trace.get('persisted_event_rows'))}; targeted events {_int(trace.get('targeted_events_seen'))}; diag {trace.get('diagnosis') or 'n/a'}.\n📡 Core API",1)
    if isinstance(relief,dict) and 'Last-chance line relief' not in text:
        text=text.replace('📡 Core API',f"• Last-chance line relief: seen {_int(relief.get('seen'))}; floor {_int(relief.get('floor_passed'))}; hard {_int(relief.get('blocked_by_hard_guard'))}; relieved {_int(relief.get('relieved'))}.\n📡 Core API",1)
    return text

def patch_line_diagnostics(text:str)->str:
    d=_load(EXPORT/'latest-line-movement-diagnostics.json',{}); c=d.get('class_counts') if isinstance(d,dict) and isinstance(d.get('class_counts'),dict) else {}
    if c and 'Line movement breakdown' not in text:
        labels={'actual_bad_movement':'реально плохое движение','selected_price_not_current':'выбранный коэффициент уже не текущий','not_confirmed':'движение не подтверждено','unconfirmed_final':'финальная проверка не подтвердила','odds_below_min':'коэффициент ниже минимума','duplicate':'дубликат','xg_direction_conflict':'конфликт направления с xG'}; parts=[f'{labels[k]} {_int(c.get(k))}' for k in labels if _int(c.get(k))>0]
        if parts: text=text.replace('🚫 Почему не опубликовано','• Line movement breakdown: '+'; '.join(parts[:8])+'.\n🚫 Почему не опубликовано',1)
    return text

def patch_conclusion(text:str)->str:
    repl='• Главный текущий стопор: line movement/freshness/current-price; Bzzoiro detail и last-chance relief теперь диагностируются отдельно.'
    text=re.sub(r'^• Главный технический bottleneck:.*$',repl,text,count=1,flags=re.MULTILINE); text=re.sub(r'^• Главный текущий стопор:.*$',repl,text,count=1,flags=re.MULTILINE); return text

def patch(payload:dict[str,Any],text:str)->tuple[dict[str,Any],str]:
    payload=patch_payload_quality(payload); payload=patch_payload(payload); text=patch_text_quality(text,payload); text=patch_runtime_lines(text); text=patch_a_tier_summary(text); text=patch_line_diagnostics(text); text=patch_conclusion(text); return payload,text
