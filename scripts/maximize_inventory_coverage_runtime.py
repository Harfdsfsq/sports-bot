from __future__ import annotations

"""Force full 300-row inventory coverage planning before reports/fallback.

No external calls are made here. The script fixes routing/counters so provider
budgets are spent on the whole top-300 inventory instead of only the tiny publish
window. It also marks explicit target gaps for 2+ lines and 2+ context sources.
"""

import json, os, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path('.').resolve(); DAY=ROOT/'.data/day_inventory'; EXP=ROOT/'.data/exports'; OUT=EXP/'latest-max-inventory-coverage-runtime.json'; UTC=timezone.utc
LIVE={'odds_api_io','bzzoiro','sportlogic'}
CTX={'sstats','bzzoiro','sportlogic','football_data','thesportsdb','api_football','espn','openfootball','openligadb','clubelo','futrixmetrics','self_history','weather'}
NON={'','market','ensemble','line_history','odds_api_io','fixture','alias','proxy','inventory','day_inventory'}

def load(p:Path,d:Any)->Any:
    try: return json.loads(p.read_text(encoding='utf-8',errors='replace'))
    except Exception: return d

def write(p:Path,x:Any)->None:
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')

def norm(v:Any)->str:
    t=re.sub(r'[^a-z0-9]+','_',str(v or '').lower()).strip('_')
    return {'oddsapiio':'odds_api_io','odds_api':'odds_api_io','bzzoiro_v2':'bzzoiro','bzzoiro_predictions':'bzzoiro','bzzoiro_current_odds':'bzzoiro','sport_logic':'sportlogic','football_data_org':'football_data','sportsdb':'thesportsdb','the_sports_db':'thesportsdb','sstats_form':'sstats'}.get(t,t)

def vals(v:Any)->list[str]:
    if isinstance(v,dict): return [str(k) for k,val in v.items() if val not in (None,'',[],{},False)]
    if isinstance(v,(list,tuple,set)): return [str(x) for x in v if str(x).strip()]
    if isinstance(v,str) and v.strip(): return [x for x in re.split(r'[,|;/\s]+',v) if x]
    return []

def containers(r:dict[str,Any])->list[dict[str,Any]]:
    out=[r]
    for k in ('coverage','metadata','source_summary','day_inventory_coverage','progressive_coverage'):
        if isinstance(r.get(k),dict): out.append(r[k])
    return out

def intish(v:Any)->int:
    try:
        if isinstance(v,(list,tuple,set,dict)): return len(v)
        return int(float(str(v))) if v not in (None,'') else 0
    except Exception: return 0

def odds_sources(r:dict[str,Any])->set[str]:
    s=set()
    for c in containers(r):
        for k in ('odds_sources','line_sources','verified_odds_sources','core_odds_sources'):
            s|={norm(x) for x in vals(c.get(k))}
        if c.get('odds') or c.get('has_odds') or c.get('price_confirmations') or c.get('books_count'):
            src=norm(c.get('source') or c.get('provider') or '')
            if src in LIVE: s.add(src)
    return {x for x in s if x in LIVE}

def ctx_sources(r:dict[str,Any])->set[str]:
    s=set()
    for c in containers(r):
        for k in ('context_sources','context_confirmations','verified_context_sources','all_context_sources','core_context_sources','confirmation_sources','runtime_context_bridge_sources'):
            s|={norm(x) for x in vals(c.get(k))}
        for flag,src in (('sstats_has_context_hint','sstats'),('bzzoiro_has_context_hint','bzzoiro'),('sportlogic_context','sportlogic'),('weather','weather')):
            if c.get(flag): s.add(src)
    return {x for x in s if x in CTX and x not in NON and not re.match(r'^context_(source|confirmation)_\d+$',x)}

def price_count(r:dict[str,Any])->int:
    best=0
    for c in containers(r):
        for k in ('price_confirmations','price_confirmation_sources_count','price_sources_count','books_count','bookmaker_count','latest_books_max'):
            best=max(best,intish(c.get(k)))
        best=max(best,len(vals(c.get('books'))),len(vals(c.get('bookmakers'))))
    return best

def target_date()->str:
    return os.getenv('DAY_INVENTORY_TARGET_DATE') or datetime.now(UTC).astimezone().date().isoformat()

def main()->int:
    d=target_date(); paths=[DAY/f'{d}.json',DAY/'today.json',DAY/'current.json',DAY/'latest.json']
    payload={}
    for p in paths:
        x=load(p,{})
        if isinstance(x,dict) and isinstance(x.get('matches'),list) and x['matches']:
            payload=x; break
    rows=[dict(x) for x in payload.get('matches',[]) if isinstance(x,dict)][:300]
    now=datetime.now(UTC).isoformat(); gaps=[]; changed=0
    for i,r in enumerate(rows):
        before=json.dumps(r,ensure_ascii=False,sort_keys=True)
        osrc=odds_sources(r); csrc=ctx_sources(r); pc=price_count(r)
        cov=r.setdefault('coverage',{}); md=r.setdefault('metadata',{})
        r['odds_sources']=sorted(osrc); r['context_sources']=sorted(csrc); r['context_confirmations']=sorted(csrc)
        md['odds_sources_count']=max(intish(md.get('odds_sources_count')),len(osrc)); md['context_sources_count']=max(intish(md.get('context_sources_count')),len(csrc)); md['price_confirmation_sources_count']=max(intish(md.get('price_confirmation_sources_count')),pc)
        cov['odds']=bool(osrc or pc); cov['context']=bool(csrc); cov['ready_for_model']=bool(cov.get('odds') and cov.get('context'))
        r['coverage_targets']={'target_rank':i+1,'need_odds_sources':max(0,2-len(osrc)),'need_context_sources':max(0,2-len(csrc)),'need_price_confirmations':max(0,2-pc),'target_2plus_lines_and_context':True}
        if any(r['coverage_targets'][k]>0 for k in ('need_odds_sources','need_context_sources','need_price_confirmations')): gaps.append({'match_key':r.get('match_key') or r.get('canonical_match_id'),'home_team':r.get('home_team'),'away_team':r.get('away_team'),**r['coverage_targets']})
        changed+=int(before!=json.dumps(r,ensure_ascii=False,sort_keys=True))
    counts={'matches_total':len(rows),'matches_with_odds':sum(1 for r in rows if (r.get('coverage') or {}).get('odds')),'matches_with_context':sum(1 for r in rows if (r.get('coverage') or {}).get('context')),'matches_with_2plus_odds_sources':sum(1 for r in rows if len(r.get('odds_sources') or [])>=2),'matches_with_2plus_context_sources':sum(1 for r in rows if len(r.get('context_sources') or [])>=2),'matches_with_2plus_price_confirmations':sum(1 for r in rows if intish((r.get('metadata') or {}).get('price_confirmation_sources_count'))>=2),'matches_ready_for_model':sum(1 for r in rows if (r.get('coverage') or {}).get('ready_for_model')),'matches_ready_2plus_lines_and_context':sum(1 for r in rows if len(r.get('odds_sources') or [])>=2 and len(r.get('context_sources') or [])>=2 and intish((r.get('metadata') or {}).get('price_confirmation_sources_count'))>=2)}
    payload.update({'matches':rows,'counts':{**dict(payload.get('counts') or {}),**counts},'updated_at_utc':now})
    for p in paths: write(p,payload)
    report={'status':'ok','updated_at_utc':now,'rows_seen':len(rows),'rows_changed':changed,'counts':counts,'gap_count':len(gaps),'gap_examples':gaps[:40],'notes':['Target is full top-300 coverage with 2+ independent line sources, 2+ context sources and 2+ price confirmations.','This script only fixes inventory routing and gap targets; providers still need quota/entitlements to fill every gap.']}
    write(OUT,report); write(EXP/'latest-day-inventory-summary.json',{'date_local':d,'updated_at_utc':now,'counts':payload['counts'],'sources':{**dict(payload.get('sources') or {}),'max_inventory_coverage_runtime':report}})
    print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
