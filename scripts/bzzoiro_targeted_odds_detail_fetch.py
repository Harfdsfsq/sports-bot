from __future__ import annotations

import asyncio, json, os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import httpx

EXPORT=Path('.data/exports'); INP=EXPORT/'latest-bzzoiro-targeted-odds-confirmation.json'; OUT=EXPORT/'latest-bzzoiro-targeted-odds-detail.json'; ODDS=EXPORT/'latest-bzzoiro-odds.json'; RAW=EXPORT/'latest-bzzoiro-odds-raw.json'

def _load(p:Path,d:Any=None)->Any:
    try: return json.loads(p.read_text(encoding='utf-8',errors='replace'))
    except Exception: return {} if d is None else d

def _write(p:Path,payload:Any)->None:
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')

def _num(v:Any)->float|None:
    try:
        if v in (None,''): return None
        x=float(str(v).replace(',','.')); return x if x>1.0 else None
    except Exception: return None

def _eid_from_raw(raw:dict[str,Any])->str:
    for k in ('id','api_id','event_id','source_event_id','bzzoiro_event_id'):
        v=raw.get(k)
        if v not in (None,''): return str(v)
    return ''

def _event_id(row:dict[str,Any])->str:
    raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
    return _eid_from_raw(raw) or _eid_from_raw(row)

def _offers_from_payload(payload:Any,*,event_id:str,match_key:str='')->list[dict[str,Any]]:
    offers=[]
    def add(fam:str,sel:str,price:Any,point:float|None=None,book:str='bzzoiro-detail'):
        p=_num(price)
        if p is not None: offers.append({'source':'bzzoiro','bookmaker':book,'family':fam,'selection':sel,'price':p,'point':point,'source_event_id':event_id,'match_key':match_key})
    def walk(o:Any,market_hint:str='',outcome_hint:str='',book_hint:str=''):
        if isinstance(o,dict):
            compact=o.get('odds') if isinstance(o.get('odds'),dict) else o
            add('h2h','Home',compact.get('home_win') or compact.get('home') or compact.get('odds_home') or compact.get('home_odds'))
            add('h2h','Draw',compact.get('draw') or compact.get('odds_draw') or compact.get('draw_odds'))
            add('h2h','Away',compact.get('away_win') or compact.get('away') or compact.get('odds_away') or compact.get('away_odds'))
            for point,suffs in ((1.5,('15','1_5')), (2.5,('25','2_5')), (3.5,('35','3_5'))):
                for s in suffs:
                    add('totals','Over',compact.get(f'over_{s}_goals') or compact.get(f'odds_over_{s}'),point)
                    add('totals','Under',compact.get(f'under_{s}_goals') or compact.get(f'odds_under_{s}'),point)
            market=str(o.get('market') or o.get('market_key') or o.get('market_name') or market_hint or ''); outcome=str(o.get('outcome') or o.get('selection') or o.get('name') or outcome_hint or ''); book=str(o.get('bookmaker') or o.get('bookmaker_name') or o.get('bookmaker_slug') or book_hint or 'bzzoiro-detail')
            price=o.get('price') or o.get('decimal') or o.get('decimal_odds')
            try: point=float(str(o.get('line') or o.get('point')).replace(',','.')) if o.get('line') or o.get('point') else None
            except Exception: point=None
            mn=market.lower(); on=outcome.lower()
            if price not in (None,'') and outcome:
                if 'total' in mn or 'over' in mn or 'goal' in mn:
                    if 'under' in on or on.startswith('u'): add('totals','Under',price,point,book)
                    elif 'over' in on or on.startswith('o'): add('totals','Over',price,point,book)
                elif mn in {'1x2','h2h','matchwinner','match winner'} or 'winner' in mn:
                    if on in {'home','1','home win','home_win'}: add('h2h','Home',price,None,book)
                    elif on in {'draw','x'}: add('h2h','Draw',price,None,book)
                    elif on in {'away','2','away win','away_win'}: add('h2h','Away',price,None,book)
            for k,v in o.items():
                if isinstance(v,(dict,list)): walk(v,market or str(k),outcome,book)
        elif isinstance(o,list):
            for i in o: walk(i,market_hint,outcome_hint,book_hint)
    walk(payload); seen=set(); out=[]
    for o in offers:
        key=(o.get('bookmaker'),o.get('family'),o.get('selection'),o.get('point'),o.get('price'),o.get('source_event_id'))
        if key not in seen: seen.add(key); out.append(o)
    return out

async def _fetch(client:httpx.AsyncClient,base:str,eid:str,headers:dict[str,str],path:str)->Any:
    try:
        r=await client.get(f'{base}/events/{eid}/{path}/',headers=headers)
        return r.json() if r.status_code==200 else {'http_status':r.status_code,'body_preview':r.text[:300]}
    except Exception as e: return {'error':f'{type(e).__name__}: {e}'}

async def _main()->int:
    conf=_load(INP,{}); confirmations=conf.get('confirmations') if isinstance(conf.get('confirmations'),list) else []
    targets=[]; seen=set()
    for row in confirmations:
        if not isinstance(row,dict): continue
        eid=_event_id(row)
        if eid and eid not in seen: seen.add(eid); targets.append({'event_id':eid,'match_key':row.get('match_key'),'home_team':row.get('home_team'),'away_team':row.get('away_team')})
    targets=targets[:max(0,int(float(os.getenv('BZZOIRO_TARGETED_ODDS_DETAIL_LIMIT','60') or 60)))]
    key=os.getenv('BZZOIRO_API_KEY'); base=(os.getenv('BZZOIRO_BASE_URL') or 'https://sports.bzzoiro.com/api/v2').rstrip('/'); details=[]; all_offers=[]; requests=0
    if key and targets:
        headers={'Authorization':f'Token {key}'}
        async with httpx.AsyncClient(timeout=float(os.getenv('BZZOIRO_TIMEOUT_SECONDS','20') or 20),follow_redirects=True) as client:
            for t in targets:
                eid=str(t['event_id']); payloads={}
                for endpoint in ('odds','odds/comparison'):
                    payloads[endpoint]=await _fetch(client,base,eid,headers,endpoint); requests+=1
                offers=[]
                for p in payloads.values(): offers.extend(_offers_from_payload(p,event_id=eid,match_key=str(t.get('match_key') or '')))
                all_offers.extend(offers); details.append({**t,'offers':len(offers),'sample_offers':offers[:8],'payload_shapes':{k:type(v).__name__ for k,v in payloads.items()},'endpoint_statuses':{k:(v.get('http_status') if isinstance(v,dict) else 200) for k,v in payloads.items()}})
    diagnosis='missing_bzzoiro_api_key' if not key else ('no_matched_event_ids' if not targets else ('offers_parsed' if all_offers else 'detail_endpoints_returned_no_parseable_offers'))
    payload={'status':'ok','created_at_utc':datetime.now(UTC).isoformat(),'targets':len(targets),'requests':requests,'events_with_offers':sum(1 for d in details if d.get('offers')),'offers':len(all_offers),'details':details,'publication_contract_relaxed':False,'diagnosis':diagnosis}
    _write(OUT,payload); _write(RAW,{'created_at_utc':payload['created_at_utc'],'source':'bzzoiro','rows':all_offers,'offer_count':len(all_offers),'diagnosis':diagnosis}); _write(ODDS,{'created_at_utc':payload['created_at_utc'],'source':'bzzoiro','rows':all_offers,'offer_count':len(all_offers),'diagnosis':diagnosis})
    # Merge diagnostics back into targeted confirmation so Telegram report sees post-detail truth.
    if isinstance(conf,dict):
        conf['odds_detail']={k:payload[k] for k in ('targets','requests','events_with_offers','offers','diagnosis')}
        if all_offers:
            conf['offers']=max(int(conf.get('offers') or 0),len(all_offers)); conf['diagnosis']='detail_offers_parsed';
            conf['two_source_promoted']=max(int(conf.get('two_source_promoted') or 0), sum(1 for c in confirmations if isinstance(c,dict) and int(c.get('target_odds_sources') or 0)<2))
        else:
            conf['diagnosis']=diagnosis if str(conf.get('diagnosis') or '').startswith('matched') else conf.get('diagnosis') or diagnosis
        _write(INP,conf)
    print(json.dumps({k:payload[k] for k in ('targets','requests','events_with_offers','offers','diagnosis')},ensure_ascii=False)); return 0

def main()->int: return asyncio.run(_main())
if __name__=='__main__': raise SystemExit(main())
