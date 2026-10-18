from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.goal_research import GoalHistory, result_row, normalize_result, fit_model, predict, over25

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)


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
