from __future__ import annotations

import json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path('.').resolve(); POLICY_PATH=ROOT/'config/provider_request_budget.json'; OUT=ROOT/'.data/exports/latest-odds-budget-boost-policy.json'; GITHUB_ENV=os.getenv('GITHUB_ENV'); UTC=timezone.utc

def load_json(path:Path,default:Any)->Any:
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def write_json(path:Path,payload:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')

def append_env(env:dict[str,str])->None:
    if not GITHUB_ENV:
        for k in sorted(env): print(f'{k}={env[k]}')
        return
    with open(GITHUB_ENV,'a',encoding='utf-8') as fh:
        for k in sorted(env): fh.write(f'{k}={env[k]}\n')

def patch(providers:dict[str,Any],name:str,env:dict[str,str],grant:int)->None:
    row=providers.setdefault(name,{})
    if not isinstance(row,dict): row={}; providers[name]=row
    row['per_run_max']=grant; row['min_spacing_minutes']=0
    old=dict(row.get('env') or {}); old.update(env); row['env']=old

def main()->int:
    policy=load_json(POLICY_PATH,{}) if isinstance(load_json(POLICY_PATH,{}),dict) else {}
    providers=policy.setdefault('providers',{})
    env={
      'RULES_API_BUDGET_POLICY_VERSION':'v26-full-300-two-plus-coverage',
      'ALL_SOURCES_FREE_MAXIMIZE':'true','CONTEXT_ENRICHMENT_REQUIRES_OFFERS':'false','CONTEXT_ENRICHMENT_MATCH_LIMIT':'300','PREMIUM_CONTEXT_SHORTLIST_LIMIT':'300',
      'DAY_INVENTORY_TARGET_SIZE':'300','DAY_INVENTORY_MAX_MATCHES':'300','DAY_INVENTORY_FORCE_FULL_300':'true','DAY_INVENTORY_FORCE_TOP_300':'true','DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES':'300',
      'HARIZON_FULL_INVENTORY_PROVIDER_TARGETS':'300','HARIZON_RUNTIME_MATCH_RECOVERY_MIN_FILTERED_MATCHES':'300','HARIZON_RUNTIME_MATCH_RECOVERY_MAX_MATCHES':'300','HARIZON_RUNTIME_MATCH_RECOVERY_WINDOW_HOURS':'36',
      'ODDS_API_IO_PER_RUN_MAX':'200','ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN':'200','ODDS_API_IO_REQUESTS_MAX_PER_RUN':'200','ODDS_API_IO_ACCOUNT1_PER_RUN_MAX':'100','ODDS_API_IO_ACCOUNT2_PER_RUN_MAX':'100','ODDS_API_IO_MATCH_LIMIT':'300','ODDS_API_IO_ODDS_MATCH_LIMIT':'300','ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN':'300','MAX_MATCHES_FOR_ODDS_FETCH':'520','ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT':'36',
      'BZZOIRO_PER_RUN_MAX':'180','BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN':'180','BZZOIRO_CONTEXT_MATCH_LIMIT':'300','BZZOIRO_ODDS_MATCH_LIMIT':'300','BZZOIRO_TARGETED_ODDS_DETAIL_LIMIT':'160','BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT':'160','BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS':'160','BZZOIRO_RUNTIME_PROVIDER_DEADLINE_SECONDS':'220',
      'SSTATS_PER_RUN_MAX':'150','SSTATS_REQUESTS_MAX_PER_RUN':'150','SSTATS_MAX_HTTP_REQUESTS_PER_RUN':'150','SSTATS_CONTEXT_MATCH_LIMIT':'300',
      'SPORTLOGIC_ENABLED':'true','ENABLE_SPORTLOGIC':'true','SPORTLOGIC_CONTROLLED_ODDS_ENABLED':'true','SPORTLOGIC_PER_RUN_MAX':'120','SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN':'120','SPORTLOGIC_MATCH_LIMIT':'300','SPORTLOGIC_CONTEXT_MATCH_LIMIT':'300','SPORTLOGIC_ODDS_MATCH_LIMIT':'160','SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY':'false',
      'FOOTBALL_DATA_PER_RUN_MAX':'24','FOOTBALL_DATA_REQUESTS_MAX_PER_RUN':'24','FOOTBALL_DATA_CONTEXT_MATCH_LIMIT':'300','THESPORTSDB_PER_RUN_MAX':'30','THESPORTSDB_REQUESTS_MAX_PER_RUN':'30','THESPORTSDB_CONTEXT_MATCH_LIMIT':'300','OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN':'18','OPENFOOTBALL_CONTEXT_MATCH_LIMIT':'300','WEATHERAPI_PER_RUN_MAX':'32','WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN':'32','WEATHER_CONTEXT_MATCH_LIMIT':'300'
    }
    patch(providers,'odds_api_io',{k:v for k,v in env.items() if k.startswith('ODDS_API_IO') or k=='MAX_MATCHES_FOR_ODDS_FETCH'},200)
    patch(providers,'bzzoiro',{k:v for k,v in env.items() if k.startswith('BZZOIRO')},180)
    patch(providers,'sstats',{k:v for k,v in env.items() if k.startswith('SSTATS')},150)
    patch(providers,'sportlogic',{k:v for k,v in env.items() if k.startswith('SPORTLOGIC') or k=='ENABLE_SPORTLOGIC'},120)
    policy['version']='v26-full-300-two-plus-coverage'; write_json(POLICY_PATH,policy); append_env(env)
    report={'status':'ok','updated_at_utc':datetime.now(UTC).isoformat(),'version':policy['version'],'env':env,'reason':'Full top-300 coverage routing; no legacy downgrade to 80 requests.'}
    write_json(OUT,report); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
