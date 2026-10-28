from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.goal_research import GoalHistory, result_row, normalize_result, fit_model, predict, over25

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)


@pytest.mark.parametrize('case', ['alias', 'shift', 'conflict', 'ambiguous', 'exact_conflict', 'exact_consistent', 'exact_disagrees', 'wrong_squad', 'wrong_time'])
def test_result_recovery_requires_unique_safe_identity(tmp_path,case):
    h=GoalHistory(tmp_path/'history.json');kickoff=NOW-timedelta(days=1)
    f={'version':'opponent-poisson-research-v1','home':'tsg hoffenheim','away':'bayer leverkusen',
       'kickoff':kickoff.isoformat(),'created_at':(kickoff-timedelta(hours=2)).isoformat(),
       'p_over25':.5,'baseline_over25':.5,'status':'pending'}
    h.data['forecasts']['f']=f
    r=result_row('League','TSG 1899 Hoffenheim' if case!='wrong_squad' else 'TSG Hoffenheim U19',
                 'Bayer Leverkusen',kickoff+timedelta(minutes=5 if case=='shift' else 60 if case=='wrong_time' else 0),2,1,'test')
    r['observed_at']=NOW.isoformat();r['conflict']=case=='conflict';h.data['results'][r['id']]=r
    if case in {'ambiguous','exact_conflict','exact_consistent','exact_disagrees'}:
        other=dict(r,id='other',kickoff=(kickoff+timedelta(minutes=1)).isoformat());h.data['results']['other']=other
        if case.startswith('exact_'):
            f['home']=r['home']
            other['conflict']=case=='exact_conflict'
            if case=='exact_disagrees':other['hg']=5
    summary=h.run_research([],NOW)
    assert summary['resolved']==(1 if case in {'alias','shift','exact_consistent'} else 0)
    if case in {'alias','shift','exact_consistent'}:assert f['total']==3
    else:assert summary['overdue_reasons'] and 'total' not in f


def test_result_backfill_is_bounded_and_keeps_conflicts_quarantined(tmp_path,monkeypatch):
    import asyncio
    from app.services.settlement import SettlementService
    h=GoalHistory(tmp_path/'history.json')
    for i in range(1,5):
        dt=NOW-timedelta(days=i)
        h.data['forecasts'][str(i)]={'version':'test','home':'Home','away':'Away','kickoff':dt.isoformat(),
                                    'created_at':(dt-timedelta(hours=1)).isoformat(),'status':'pending'}
    calls=[]
    async def fetch(service,start,end,*,max_requests=None):
        calls.append((start,end,max_requests));service.sstats_fetch_stats={'requests':1,'complete':True}
        return []
    monkeypatch.setattr(SettlementService,'_fetch_sstats_rows',fetch)
    settings=SimpleNamespace(sstats_api_key='fake')
    assert len(asyncio.run(h.refresh_overdue_results(settings,NOW))['dates'])==2
    assert len(asyncio.run(h.refresh_overdue_results(settings,NOW))['dates'])==2
    assert len(asyncio.run(h.refresh_overdue_results(settings,NOW))['dates'])==0
    assert len(calls)==4 and all(a==b and cap==3 for a,b,cap in calls)
    assert all(f['status']=='pending' for f in h.data['forecasts'].values())


def dataset():
    # Distinct identities and dates; attack/defence effects are recoverable.
    return [result_row('League', f'Team {h}', f'Team {a}', NOW - timedelta(days=100-i),
                       3 if h == 0 else 1, 2 if a == 0 else 0, 'test')
            for i, (h,a) in enumerate(( (h,a) for _ in range(8) for h in range(4) for a in range(4) if h != a ))]


def test_normalization_uses_regulation_not_et_and_rejects_unfinished():
    row={'status':'FINISHED','utcDate':(NOW-timedelta(days=1)).isoformat(),
         'homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},'competition':{'code':'League'},
         'score':{'duration':'EXTRA_TIME','regularTime':{'home':1,'away':1},'fullTime':{'home':3,'away':2}}}
    assert normalize_result('football_data',row)['hg'] == 1
    row['status']='IN_PLAY'
    assert normalize_result('football_data',row) is None
    row={'matchIsFinished':True,'team1':{'teamName':'Home'},'team2':{'teamName':'Away'},
         'matchDateTimeUTC':(NOW-timedelta(days=1)).isoformat(),
         'matchResults':[{'resultTypeKind':'HalfTime','pointsTeam1':0,'pointsTeam2':0}]}
    assert normalize_result('openligadb',row,'League') is None
    row={'statusName':'Finished','homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},
         'date':(NOW-timedelta(days=1)).isoformat(),'homeFTResult':1,'awayFTResult':2,
         'homeResult':5,'awayResult':4,'season':{'league':{'id':123}}}
    r=normalize_result('sstats',row)
    assert (r['hg'],r['ag'],r['league']) == (1,2,'sstats:123')
    row['homeFTResult']=None
    assert normalize_result('sstats',row) is None


def test_history_deduplicates_and_quarantines_conflicts(tmp_path):
    h=GoalHistory(tmp_path/'history.json')
    raw={'statusName':'Finished','homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},
         'date':(NOW-timedelta(days=1)).isoformat(),'homeFTResult':1,'awayFTResult':2,'season':{'league':{'id':123}}}
    assert h.ingest('sstats',[raw,raw],NOW)==1
    assert len(h.data['results'])==1
    raw['homeFTResult']=3
    h.ingest('sstats',[raw],NOW)
    assert h.eligible('sstats:123',NOW)==[]
    assert next(iter(h.data['results'].values()))['conflict']
    assert GoalHistory(h.path).data==h.data


def test_fit_uses_opponents_and_excludes_future_results():
    rows=dataset()
    m=fit_model(rows,NOW)
    assert predict(m,'Team 0','Team 1')[0] > predict(m,'Team 1','Team 2')[0]
    future=result_row('League','Team 0','Team 1',NOW+timedelta(days=1),0,20,'test')
    assert fit_model(rows+[future],NOW)['beta']==m['beta']
    assert predict(m,'Missing','Team 1') is None
    assert over25(4)>over25(2)


def test_prospective_forecast_is_frozen_and_scored_only_after_result(tmp_path):
    history=GoalHistory(tmp_path/'history.json')
    for r in dataset():
        r['observed_at']=(NOW-timedelta(days=1)).isoformat()
        history.data['results'][r['id']]=r
    fixture=SimpleNamespace(home_team='Team 0',away_team='Team 1',league_name='League',commence_time=NOW+timedelta(hours=2))
    s=history.run_research([fixture],NOW)
    assert s['new_forecasts']==1 and s['resolved']==0
    first=next(iter(history.data['forecasts'].values())).copy()
    assert history.run_research([fixture],NOW+timedelta(minutes=10))['new_forecasts']==0
    assert next(iter(history.data['forecasts'].values()))==first
    r=result_row('League','Team 0','Team 1',fixture.commence_time,2,1,'test')
    r['observed_at']=(NOW+timedelta(hours=5)).isoformat()
    history.data['results'][r['id']]=r
    s=history.run_research([],NOW+timedelta(hours=5))
    assert s['resolved']==1 and s['brier'] is not None and s['baseline_brier'] is not None
    r['conflict']=True
    assert history.run_research([],NOW+timedelta(hours=6))['resolved']==0


def test_backtest_training_is_strictly_earlier(monkeypatch,tmp_path):
    from scripts import evaluate_goal_research as script
    history=GoalHistory(tmp_path/'history.json')
    for r in dataset():
        r['observed_at']=NOW.isoformat();history.data['results'][r['id']]=r
    original=script.fit_model
    def checked(rows,cutoff):
        from app.services.daily_match_registry import parse_time
        assert all(parse_time(r['kickoff']) < cutoff-timedelta(hours=3) for r in rows)
        return original(rows,cutoff)
    monkeypatch.setattr(script,'fit_model',checked)
    assert script.evaluate(history,10)['evaluated'] > 0


@pytest.mark.parametrize('side',['home','away'])
def test_daily_sstats_explicit_ft_and_null_do_not_fall_back(monkeypatch,side):
    from app.providers.sstats import SStatsContextProvider
    monkeypatch.setenv('PUBLICATION_PROFILE','daily_quality')
    row={side+'FTResult':1,side+'Result':4}
    assert SStatsContextProvider._extract_result(row,side)==1
    row[side+'FTResult']=None
    assert SStatsContextProvider._extract_result(row,side) is None
    assert SStatsContextProvider._extract_result({side+'Result':4,'statusName':'AfterExtraTime'},side) is None
    monkeypatch.delenv('PUBLICATION_PROFILE')
    assert SStatsContextProvider._extract_result(row,side)==4


def test_close_kickoff_duplicates_do_not_inflate_training(tmp_path):
    h=GoalHistory(tmp_path/'history.json')
    row={'statusName':'Finished','homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},
         'date':(NOW-timedelta(days=1)).isoformat(),'homeFTResult':1,'awayFTResult':2,'season':{'league':{'id':123}}}
    assert h.ingest('sstats',[row],NOW)==1
    row['date']=(NOW-timedelta(days=1)+timedelta(minutes=5)).isoformat()
    assert h.ingest('sstats',[row],NOW)==0
    assert len(h.data['results'])==1


def test_disconnected_opponents_are_not_comparable():
    rows=dataset()
    for i in range(36):
        rows.append(result_row('League','Other A','Other B',NOW-timedelta(days=i+1),2,0,'test'))
    model=fit_model(rows,NOW)
    assert predict(model,'Team 0','Other A') is None


def test_archive_failure_does_not_disable_provider(monkeypatch):
    import app.services.goal_research as module
    monkeypatch.setattr(module.GoalHistory,'ingest',lambda *args: (_ for _ in ()).throw(OSError('disk')))
    assert module.archive_results('sstats',[])=={'status':'error','error_type':'OSError'}


def test_research_old_alias_keys_keep_first_prediction_and_single_metric(tmp_path):
    from app.services.goal_research import VERSION
    h=GoalHistory(tmp_path/'history.json')
    kickoff=NOW-timedelta(days=2)
    base={'version':VERSION,'home':'Home','away':'Away','kickoff':kickoff.isoformat(),
          'created_at':(kickoff-timedelta(hours=2)).isoformat(),
          'status':'pending','p_over25':.2,'baseline_over25':.5}
    # Insertion order and best eventual score must not select the retained row.
    h.data['forecasts']={'alias-later':{**base,'created_at':(kickoff-timedelta(hours=1)).isoformat(),'p_over25':.9},
                         'first':base.copy()}
    r=result_row('League','Home','Away',kickoff,2,1,'test')
    # Result normalizer lowercases the names just as actual stored forecasts do.
    for f in h.data['forecasts'].values():f.update(home=r['home'],away=r['away'])
    r['observed_at']=(kickoff+timedelta(hours=3)).isoformat()
    h.data['results'][r['id']]=r
    s=h.run_research([],NOW)
    assert s['resolved']==1 and s['duplicates_ignored']==1
    assert s['brier']==pytest.approx(.64)
    assert h.data['forecasts']['alias-later']['duplicate_of']=='first'
    assert len(GoalHistory(h.path).data['forecasts'])==2


def test_new_fixture_aliases_do_not_create_second_research_prediction(tmp_path,monkeypatch):
    import app.services.goal_research as module
    h=GoalHistory(tmp_path/'history.json')
    monkeypatch.setattr(module,'fit_model',lambda *args:object())
    monkeypatch.setattr(module,'predict',lambda *args:(1.2,1.4))
    monkeypatch.setattr(module,'resolve_team',lambda model,name:'home' if 'Home' in name else 'away')
    # Model dictionary is only used for the fixed league baseline here.
    monkeypatch.setattr(module,'fit_model',lambda *args:{'league_total':2.5,'count':40})
    m=SimpleNamespace(home_team='Home FC',away_team='Away FC',league_name='League',commence_time=NOW+timedelta(hours=2))
    alias=SimpleNamespace(**{**vars(m),'home_team':'Home Football Club'})
    assert h.run_research([m,alias],NOW)['new_forecasts']==1
    assert h.run_research([alias],NOW+timedelta(minutes=10))['new_forecasts']==0


def test_pending_research_warns_on_missing_results_without_inventing_score(tmp_path):
    from app.services.goal_research import VERSION
    h=GoalHistory(tmp_path/'history.json')
    h.data['forecasts']={'old':{'version':VERSION,'home':'home','away':'away',
       'kickoff':(NOW-timedelta(hours=10)).isoformat(),'created_at':(NOW-timedelta(hours=12)).isoformat(),
       'status':'pending','p_over25':.5,'baseline_over25':.5}}
    s=h.run_research([],NOW)
    assert s['overdue_results']==1 and s['oldest_overdue_hours']==10
    assert s['resolved']==0 and s['brier'] is None
    assert 'total' not in h.data['forecasts']['old']


def recovery_forecast(kickoff=None):
    kickoff=kickoff or NOW-timedelta(days=2)
    return {'version':'opponent-poisson-research-v1','home':'home','away':'away','kickoff':kickoff.isoformat(),
            'created_at':(kickoff-timedelta(hours=2)).isoformat(),'status':'pending',
            'p_over25':.5,'baseline_over25':.5}


def install_recovery_client(monkeypatch,responder):
    from app.services import research_result_recovery as recovery
    calls=[]
    class Response:
        status_code=200
        def __init__(self,payload):self.payload=payload
        def raise_for_status(self):pass
        def json(self):return self.payload
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kwargs):
            calls.append((url,kwargs));return Response(responder(url,kwargs))
    monkeypatch.setattr(recovery,'BudgetedAsyncClient',Client)
    return calls


def test_football_data_recovers_result_without_sstats_key(tmp_path,monkeypatch):
    import asyncio
    h=GoalHistory(tmp_path/'history.json');f=recovery_forecast();h.data['forecasts']['f']=f
    def responder(url,kwargs):
        assert kwargs['params']['dateTo']=='2026-10-17'
        return {'matches':[{'id':42,'status':'FINISHED','utcDate':f['kickoff'],
                'homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},'competition':{'code':'PL'},
                'score':{'duration':'REGULAR','fullTime':{'home':1,'away':2}}}]}
    calls=install_recovery_client(monkeypatch,responder)
    s=asyncio.run(h.refresh_overdue_results(SimpleNamespace(football_data_api_key='fake'),NOW))
    assert s['alternative']['added']==1 and len(calls)==1
    assert h.run_research([],NOW)['resolved']==1
    assert h.data['forecasts']['f']['total']==3
    assert asyncio.run(h.refresh_overdue_results(SimpleNamespace(football_data_api_key='fake'),NOW))['alternative']['requests']==0


def test_addressed_recovery_limits_requests_and_checks_returned_identity(tmp_path,monkeypatch):
    import asyncio
    from app.services.research_result_recovery import recover_alternative_results
    h=GoalHistory(tmp_path/'history.json')
    for i in range(1,6):
        f=recovery_forecast(NOW-timedelta(days=i));f.update(source_ids={'sstats':str(i)},competition_name='Germany - Bundesliga');h.data['forecasts'][str(i)]=f
    def responder(url,kwargs):
        if '/Games/' in url:
            # The endpoint returns an unrelated game with the requested ID.
            return {'data':{'id':int(url.rsplit('/',1)[1]),'statusName':'Finished',
                'date':h.data['forecasts']['1']['kickoff'],'homeTeam':{'name':'Other'},'awayTeam':{'name':'Away'},
                'homeFTResult':1,'awayFTResult':0,'season':{'league':{'id':1}}}}
        return {'matches':[]} if 'football-data' in url else []
    calls=install_recovery_client(monkeypatch,responder)
    s=asyncio.run(recover_alternative_results(h,SimpleNamespace(sstats_api_key='fake',football_data_api_key='fake'),NOW))
    assert len(calls)==s['requests']==6
    assert s['added']==0 and h.data['results']=={}
    assert sum('/Games/' in url for url,_ in calls)==3
    assert sum('football-data' in url for url,_ in calls)==2
    assert sum('openligadb' in url for url,_ in calls)==1
    assert all(f['status']=='pending' for f in h.data['forecasts'].values())


def test_recovery_never_uses_et_or_wrong_squad_result():
    from app.services.research_result_recovery import relevant_rows
    f=recovery_forecast()
    row={'matchIsFinished':True,'leagueName':'Cup','matchDateTimeUTC':f['kickoff'],
         'team1':{'teamName':'Home'},'team2':{'teamName':'Away'},
         'matchResults':[{'resultTypeKind':'AfterExtraTime','pointsTeam1':3,'pointsTeam2':2}]}
    assert relevant_rows('openligadb',[row],[f])==[]
    row['matchResults']=[{'resultTypeKind':'After90Minutes','pointsTeam1':1,'pointsTeam2':1}]
    assert len(relevant_rows('openligadb',[row],[f]))==1
    row['team1']['teamName']='Home U19'
    assert relevant_rows('openligadb',[row],[f])==[]


def test_legacy_provider_binding_requires_unique_exact_fixture(tmp_path):
    from app.services.research_result_recovery import bind_source_ids
    h=GoalHistory(tmp_path/'history.json');f=recovery_forecast()
    row={k:f[k] for k in ('home','away','kickoff')}
    h.data['fixture_ids']={'sstats:1':row,'sstats:2':row}
    bind_source_ids(h,[f]);assert not f.get('source_ids')
    del h.data['fixture_ids']['sstats:2']
    bind_source_ids(h,[f]);assert f['source_ids']=={'sstats':'1'}


def test_complete_empty_date_backfill_slows_down(tmp_path,monkeypatch):
    import asyncio
    from app.services.settlement import SettlementService
    h=GoalHistory(tmp_path/'history.json');f=recovery_forecast();h.data['forecasts']['f']=f;calls=[]
    async def fetch(service,*args,**kwargs):
        calls.append(1);service.sstats_fetch_stats={'requests':1,'complete':True};return []
    monkeypatch.setattr(SettlementService,'_fetch_sstats_rows',fetch)
    install_recovery_client(monkeypatch,lambda *_:{})
    settings=SimpleNamespace(sstats_api_key='fake')
    asyncio.run(h.refresh_overdue_results(settings,NOW))
    asyncio.run(h.refresh_overdue_results(settings,NOW+timedelta(hours=6)))
    assert len(calls)==1
    asyncio.run(h.refresh_overdue_results(settings,NOW+timedelta(hours=12)))
    assert len(calls)==2
    asyncio.run(h.refresh_overdue_results(settings,NOW+timedelta(hours=30)))
    assert len(calls)==2
    asyncio.run(h.refresh_overdue_results(settings,NOW+timedelta(hours=36)))
    assert len(calls)==3


def test_recovery_auth_failure_stops_provider_and_redacts_diagnostics(tmp_path,monkeypatch):
    import asyncio,json
    from app.services import research_result_recovery as recovery
    h=GoalHistory(tmp_path/'history.json')
    for i in (1,2,3):h.data['forecasts'][str(i)]=recovery_forecast(NOW-timedelta(days=i))
    calls=[]
    class Response:
        status_code=401
        def raise_for_status(self):raise RuntimeError('secret-token-must-not-appear')
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kw):calls.append(1);return Response()
    monkeypatch.setattr(recovery,'BudgetedAsyncClient',Client)
    s=asyncio.run(recovery.recover_alternative_results(h,SimpleNamespace(football_data_api_key='secret-token-must-not-appear'),NOW))
    assert len(calls)==1 and s['checks'][0]['http_status']==401
    assert 'secret-token' not in json.dumps(s) and 'secret-token' not in h.path.read_text()
    assert asyncio.run(recovery.recover_alternative_results(h,SimpleNamespace(football_data_api_key='fake'),NOW+timedelta(hours=6)))['requests']==1


def test_alternative_score_cannot_clear_quarantined_result(tmp_path,monkeypatch):
    import asyncio
    from app.services.research_result_recovery import recover_alternative_results
    h=GoalHistory(tmp_path/'history.json');f=recovery_forecast();h.data['forecasts']['f']=f
    r=result_row('League','Home','Away',f['kickoff'],1,0,'sstats');r.update(conflict=True,observed_at=NOW.isoformat());h.data['results'][r['id']]=r
    install_recovery_client(monkeypatch,lambda *_:{'matches':[{'id':42,'status':'FINISHED','utcDate':f['kickoff'],
        'homeTeam':{'name':'Home'},'awayTeam':{'name':'Away'},'competition':{'code':'League'},
        'score':{'duration':'REGULAR','fullTime':{'home':1,'away':0}}}]})
    asyncio.run(recover_alternative_results(h,SimpleNamespace(football_data_api_key='fake'),NOW))
    assert h.run_research([],NOW)['resolved']==0
    assert r['conflict'] and f['resolution_reason']=='conflicting_result'
