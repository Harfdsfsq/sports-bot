import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.providers.bzzoiro import BzzoiroContextProvider
from app.providers.sstats import SStatsContextProvider
from app.services.bzzoiro_free_enrichment import enrich, flatten_prediction
from app.services.context_admission import admit_context
from app.schemas import Match


def fixture(now):
    return Match('test','1','soccer','League','Home','Away',now+timedelta(hours=1),'home','away','league',metadata={})


def prediction(now):
    return {'id':7,'created_at':(now-timedelta(days=5)).isoformat(),'updated_at':now.isoformat(),
            'event':{'id':42,'home_team':'Home','away_team':'Away','event_date':(now+timedelta(hours=1)).isoformat()},
            'markets':{'expected_goals':{'home':1.4,'away':1.1},'match_result':{'prob_home':45,'prob_away':30,'prob_draw':25},'over_under':{'prob_over_25':48}},
            'model':{'confidence':.7,'version':'test'}}


def test_updated_prediction_not_rejected_for_original_creation_date():
    now=datetime.now(UTC);p=BzzoiroContextProvider(SimpleNamespace())
    flat=flatten_prediction(prediction(now));c=p._prediction_to_context(flat,prediction(now)['event'],'exact')
    assert c.home_win_probability==.45
    assert c.details['bzzoiro_over25_probability']==.48
    assert admit_context('bzzoiro',fixture(now),c,now.isoformat(),now)==[]
    c.details['bzzoiro_prediction_updated_at']=(now-timedelta(days=2)).isoformat()
    assert 'context_source_stale' in admit_context('bzzoiro',fixture(now),c,now.isoformat(),now)


@pytest.mark.parametrize('team_id_mode',['explicit','objects','wrong_squad'])
def test_v2_only_enriches_exact_fixture_and_reuses_cache(tmp_path,monkeypatch,team_id_mode):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('BZZOIRO_V2_NATIVE_EXTRA_FACTS_ENABLED','false');now=datetime.now(UTC);m=fixture(now);calls=[]
    event={**prediction(now)['event'],'league_id':3,'season_id':9,'home_team_id':10,'away_team_id':11,
           'venue':{'city':'Paris','country':'France'},'is_neutral_ground':True}
    if team_id_mode!='explicit':
        event.pop('home_team_id');event.pop('away_team_id')
        event['home_team_obj']={'id':10,'name':'Home U21' if team_id_mode=='wrong_squad' else 'Home'}
        event['away_team_obj']={'id':11,'name':'Away'}
    class Response:
        status_code=200
        def __init__(self,p):self.p=p
        def json(self):return self.p
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,params,headers):
            calls.append(url)
            if '/predictions/' in url:return Response({'results':[prediction(now)],'next':None})
            if '/standings/' in url:return Response({'standings':[{'team_id':10,'position':1},{'team_id':11,'position':2}]})
            return Response(event)
    monkeypatch.setattr('app.services.bzzoiro_free_enrichment.BudgetedAsyncClient',Client)
    p=BzzoiroContextProvider(SimpleNamespace());p.api_key='test-only';contexts={};stats={'requests':0}
    asyncio.run(enrich(p,[m],contexts,stats))
    if team_id_mode=='wrong_squad':
        assert 'bzzoiro_standings' not in contexts[m.match_key].details
    else:
        assert contexts[m.match_key].details['bzzoiro_standings']['home']['position']==1
    assert contexts[m.match_key].details['bzzoiro_fixture_facts']['is_neutral_ground'] is True
    assert contexts[m.match_key].payload['event']['venue']['country']=='France'
    assert len(calls)==3
    asyncio.run(enrich(p,[m],{},stats));assert len(calls)==3
    wrong=fixture(now);wrong.home_team='Home U21'
    bad={};asyncio.run(enrich(p,[wrong],bad,stats));assert not bad
    assert not any('/odds' in url for url in calls)


def test_sstats_capped_short_pages_continue_and_repeats_stop(monkeypatch):
    p=SStatsContextProvider(SimpleNamespace(sstats_api_key='test-only'));p.max_http_requests=5
    calls=[]
    class Response:
        def __init__(self,row):self.row=row
        def json(self):return self.row
    async def request(client,url,*,params,stats):
        calls.append(params['offset']);stats['requests']+=1
        return Response([{'id':1 if params['offset']==0 else 2,'date':'2026-11-10'}])
    monkeypatch.setattr(p,'_request_with_retries',request)
    stats={'requests':0,'payload_shapes':[],'response_errors':0}
    rows=asyncio.run(p._fetch_rows_window(None,'2026-11-01','2026-11-10',stats))
    assert [r['id'] for r in rows]==[1,2]
    assert calls==[0,1,2] and stats['history_pagination_incomplete']


@pytest.mark.parametrize('status,budget,expired,expected_calls', [(403,'24',False,1),(429,'24',False,1),(200,'0',False,0),(200,'24',True,0)])
def test_v2_respects_permission_failure_and_request_budget(tmp_path,monkeypatch,status,budget,expired,expected_calls):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('BZZOIRO_V2_NATIVE_REQUEST_LIMIT',budget)
    calls=[]
    class Response:
        status_code=status
        def json(self):return {'results':[]}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*a,**kw):calls.append(1);return Response()
    monkeypatch.setattr('app.services.bzzoiro_free_enrichment.BudgetedAsyncClient',Client)
    p=BzzoiroContextProvider(SimpleNamespace());p.api_key='test-only';contexts={};stats={'requests':0}
    if expired:
        import time
        p._native_fetch_started=time.monotonic()-1000
    asyncio.run(enrich(p,[fixture(datetime.now(UTC))],contexts,stats))
    assert len(calls)==expected_calls and not contexts
    if expected_calls:assert stats['v2_native']['blocked_status']==status


def test_nonfinite_or_market_only_payload_does_not_create_context():
    now=datetime.now(UTC);data=prediction(now);data['markets']['expected_goals']={'home':float('nan'),'away':1}
    assert flatten_prediction(data) is None
    data['markets'].pop('expected_goals');assert flatten_prediction(data) is None


@pytest.mark.parametrize('lineup_status', ['confirmed','predicted'])
def test_availability_only_collects_confirmed_lineups(tmp_path,monkeypatch,lineup_status):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('BZZOIRO_V2_NATIVE_EXTRA_FACTS_ENABLED','true')
    now=datetime.now(UTC);calls=[]
    event=prediction(now)['event']
    class Response:
        status_code=200
        def __init__(self,p):self.p=p
        def json(self):return self.p
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kw):
            calls.append(url)
            if '/predictions/' in url:return Response({'results':[prediction(now)],'next':None})
            if '/availability/' in url:return Response({'event_id':42,'available':{'lineups':True},'lineup_status':lineup_status})
            if '/lineups/' in url:return Response({'event_id':42,'lineup_status':lineup_status,'lineups':{'home':['Player']}})
            if '/h2h/' in url:return Response({'recent_matches':[]})
            return Response(event)
    monkeypatch.setattr('app.services.bzzoiro_free_enrichment.BudgetedAsyncClient',Client)
    p=BzzoiroContextProvider(SimpleNamespace());p.api_key='test-only';contexts={}
    asyncio.run(enrich(p,[fixture(now)],contexts,{'requests':0}))
    assert any('/lineups/' in url for url in calls)==(lineup_status=='confirmed')
    assert ('bzzoiro_confirmed_lineups' in next(iter(contexts.values())).details)==(lineup_status=='confirmed')


def targeted_provider():
    # Some legacy tests reload/patch the provider globally. Use the actual source
    # in an isolated module so these contracts exercise its native implementation.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('targeted_sstats_contract', Path(__file__).parents[1]/'app/providers/sstats.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from app.config import Settings
    settings = Settings(_env_file=None)
    settings.enable_sstats_context = True
    settings.sstats_api_key = 'test-only'
    settings.sstats_recent_matches = 8
    return module.SStatsContextProvider(settings)


def history_rows(now):
    return [{'id':100*sid+i,'date':(now-timedelta(days=7*i+1)).isoformat(),'status':8,
             'homeTeam':{'id':sid,'name':name},'awayTeam':{'id':90+sid,'name':'Opponent '+name},
             'homeFTResult':2,'awayFTResult':1,'season':{'league':{'name':'League'}}}
            for sid,name in [(10,'Home'),(11,'Away')] for i in range(8)]


def targeted_client(monkeypatch, callback):
    class Response:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status_code = status
            self.text = '{}'
        def json(self): return self.payload
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, *, params, headers=None):
            return Response(callback(params))
    monkeypatch.setattr('app.services.sstats_targeted_history.BudgetedAsyncClient', Client)
    return Client


def test_targeted_history_improves_real_form_and_keeps_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    monkeypatch.setenv('DAILY_SSTATS_BROAD_EXTENSION_ENABLED','false')
    p=targeted_provider();now=datetime.now(UTC);m=fixture(now);rows=history_rows(now);calls=[]
    async def initial(client, start, end, stats, **kw):
        p._daily_source_observed_at=(now-timedelta(hours=4)).isoformat()
        return [r for r in rows if datetime.fromisoformat(r['date']) > now-timedelta(days=24)]
    p._fetch_rows=initial
    def response(params):
        calls.append(params)
        assert params['Team']=='10,11' and params['Ended']=='true' and params['Order']==-1
        assert params['TimeZone']==0 and 'apikey' in params
        # The live API rejected fractional seconds with HTTP 400 (run 42871752744).
        import re
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00',params['To'])
        assert datetime.fromisoformat(params['To']).microsecond==0
        return {'data':rows if params['Offset']==0 else []}
    client_type=targeted_client(monkeypatch,response)
    monkeypatch.setitem(p.fetch_context.__func__.__globals__,'BudgetedAsyncClient',client_type)
    contexts,stats,_=asyncio.run(p.fetch_context([m]))
    c=contexts[m.match_key]
    assert c.details['home_recent_count']==c.details['away_recent_count']==8
    assert c.details['sstats_history_scope']=='verified_team_ids'
    assert datetime.fromisoformat(c.details['daily_source_observed_at']) > now
    assert stats['targeted_history']['improved']==1 and len(calls)==2
    assert 'history_extension_rows' not in stats
    stamp=c.details['daily_source_observed_at']
    again,stats,_=asyncio.run(p.fetch_context([m]))
    assert again[m.match_key].details['daily_source_observed_at']==stamp
    assert stats['targeted_history']['cache_hits']==1 and len(calls)==2


@pytest.mark.parametrize('change',['duplicate_name_id','id_wrong_squad','unknown_squad'])
def test_targeted_binding_refuses_ambiguous_ids_and_squads(change):
    from app.services.sstats_targeted_history import team_bindings
    from copy import deepcopy
    now=datetime.now(UTC);m=fixture(now);rows=history_rows(now)
    if change=='duplicate_name_id':
        extra=deepcopy(rows[0]);extra['homeTeam']['id']=999;rows.append(extra)
    elif change=='id_wrong_squad':
        extra=deepcopy(rows[0]);extra['homeTeam']['name']='Home U21';rows.append(extra)
    else:
        m.home_team='Home U21'
    assert '10' not in team_bindings([m],rows)


@pytest.mark.parametrize('change',['wrong_id','wrong_squad','live','future','nan','conflicting_score'])
def test_targeted_history_rejects_bad_rows(tmp_path,monkeypatch,change):
    from app.services.sstats_targeted_history import enrich_team_form
    from copy import deepcopy
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    p=targeted_provider();now=datetime.now(UTC);seed=history_rows(now);rows=deepcopy(seed)
    for r in rows:
        if r['homeTeam']['id']!=10:continue
        if change=='wrong_id':r['homeTeam']['id']=999
        elif change=='wrong_squad':r['homeTeam']['name']='Home U21'
        elif change=='live':r['status']=3
        elif change=='future':r['date']=(now+timedelta(minutes=10)).isoformat()
        elif change=='nan':r['homeFTResult']='nan'
    if change=='conflicting_score':
        rows=deepcopy(seed)
        for r in seed[:8]:
            extra=deepcopy(r);extra['homeFTResult']=3;rows.append(extra)
    targeted_client(monkeypatch,lambda params: {'data':rows if params['Offset']==0 else []})
    stats={'requests':0}
    result=asyncio.run(enrich_team_form(p,[fixture(now)],seed,stats,{'team_form_examples':[]}))
    assert not result
    assert stats['targeted_history']['rejected_rows'] or stats['targeted_history']['conflicting_ids']


def test_targeted_history_deduplicates_games_not_metadata(tmp_path,monkeypatch):
    from app.services.sstats_targeted_history import enrich_team_form
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    p=targeted_provider();now=datetime.now(UTC);seed=history_rows(now)
    rows=[seed[0],seed[8]]
    rows += [{**r,'metadata':i} for i in range(10) for r in rows[:2]]
    targeted_client(monkeypatch,lambda params: {'data':rows if params['Offset']==0 else []})
    stats={'requests':0}
    result=asyncio.run(enrich_team_form(p,[fixture(now)],seed,stats,{'team_form_examples':[]}))
    assert not result and stats['targeted_history']['rows']==2


@pytest.mark.parametrize('limit,expected',[('0',0),('1',1),('99',2)])
def test_targeted_request_ceiling_and_repeated_page(tmp_path,monkeypatch,limit,expected):
    from app.services.sstats_targeted_history import enrich_team_form
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    monkeypatch.setenv('DAILY_SSTATS_TARGETED_REQUEST_LIMIT',limit)
    p=targeted_provider();now=datetime.now(UTC);seed=history_rows(now);calls=[]
    def response(params):calls.append(params);return {'data':seed}
    targeted_client(monkeypatch,response)
    stats={'requests':0}
    asyncio.run(enrich_team_form(p,[fixture(now)],seed,stats,{'team_form_examples':[]}))
    assert stats['targeted_history']['requests']==len(calls)==expected
    assert not list((tmp_path/'.data/provider_cache').glob('sstats-targeted*'))


def test_native_retry_cannot_exceed_targeted_ceiling():
    p=targeted_provider()
    class Client:
        async def get(self,*args,**kwargs):raise OSError('temporary')
    p.settings=SimpleNamespace(sstats_retry_backoff_seconds=.001,sstats_request_retries=2)
    stats={'requests':0}
    assert asyncio.run(p._request_with_retries(Client(),p.url,params={},stats=stats,request_ceiling=1)) is None
    assert stats['requests']==1


def test_bzz_empty_discovery_explains_shape_and_unmatched_targets(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    class Response:
        status_code=200
        def json(self):return {'count':0,'results':[],'next':None}
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr('app.services.bzzoiro_free_enrichment.BudgetedAsyncClient',Client)
    p=BzzoiroContextProvider(SimpleNamespace());p.api_key='test-only';stats={'requests':0}
    asyncio.run(enrich(p,[fixture(datetime.now(UTC))],{},stats))
    report=stats['v2_native']
    assert report['prediction_records']==0 and report['fixtures_matched']==0
    assert report['skip_reasons']=={'no_exact_fixture':1}
    assert report['response_shape']==['count','next','results']
    assert report['target_sample'][0]['home']=='Home'


def test_report_distinguishes_targeted_from_global_history():
    from scripts.send_daily_quality_report import render
    text=render({'daily_quality': {'coverage': {}}, 'source_stats': {'sstats': {
        'history_extension_targets':3, 'targeted_history': {
            'targets':3, 'improved':2, 'bound_teams':6, 'requests':2}}}})
    assert 'адресный добор' in text and 'для 2 из 3' in text
    assert 'расширенный добор' not in text


def test_targeted_history_cannot_fill_unknown_opponent_identity(tmp_path,monkeypatch):
    from app.services.sstats_targeted_history import enrich_team_form
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    p=targeted_provider();now=datetime.now(UTC);rows=history_rows(now)[:8]
    def unexpected(params):raise AssertionError('No request for an incompletely bound fixture')
    targeted_client(monkeypatch,unexpected)
    stats={'requests':0}
    result=asyncio.run(enrich_team_form(p,[fixture(now)],rows,stats,{'team_form_examples':[]}))
    assert not result and stats['targeted_history']['stop_reason']=='no_fully_bound_fixture'


def test_targeted_history_rejects_target_name_on_wrong_opponent_id(tmp_path,monkeypatch):
    from app.services.sstats_targeted_history import enrich_team_form
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    p=targeted_provider();now=datetime.now(UTC);seed=history_rows(now)
    rows=[{**r,'awayTeam':{'id':999,'name':'Away'}} for r in seed[:8]]
    targeted_client(monkeypatch,lambda params: {'data':rows if params['Offset']==0 else []})
    stats={'requests':0}
    result=asyncio.run(enrich_team_form(p,[fixture(now)],seed,stats,{'team_form_examples':[]}))
    assert not result and stats['targeted_history']['rejected_rows']==8
