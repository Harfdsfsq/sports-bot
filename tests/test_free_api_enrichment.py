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


def test_v2_only_enriches_exact_fixture_and_reuses_cache(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('BZZOIRO_V2_NATIVE_EXTRA_FACTS_ENABLED','false');now=datetime.now(UTC);m=fixture(now);calls=[]
    event={**prediction(now)['event'],'league_id':3,'season_id':9,'home_team_id':10,'away_team_id':11,
           'venue':{'city':'Paris','country':'France'}}
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
    assert contexts[m.match_key].details['bzzoiro_standings']['home']['position']==1
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
