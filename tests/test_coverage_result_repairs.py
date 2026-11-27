from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.providers.sstats import SStatsContextProvider, form_team_key
from app.services.goal_research import GoalHistory
from app.services.research_result_recovery import bind_source_ids


def test_reviewed_alias_resolves_form_without_weakening_unknown_name():
    # Other legacy tests install permanent class monkey-patches. Exercise the
    # native implementation used when LEGACY_RUNTIME_EXTENSIONS_ENABLED=false.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('native_sstats_regression', Path(__file__).resolve().parents[1] / 'app/providers/sstats.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    p=module.SStatsContextProvider.__new__(module.SStatsContextProvider)
    assert p._resolve_team_key('JS Djidjel', {'js jijel'}, {}) == 'js jijel'
    assert p._resolve_team_key('RC Arbaa', {'rc arba'}, {}) == 'rc arba'
    assert p._resolve_team_key('JS Djidjel U21', {'js jijel'}, {}) is None
    assert p._resolve_team_key('Unknown United', {'manchester united'}, {}) is None


def test_form_identity_never_drops_squad_offline(monkeypatch):
    monkeypatch.delenv('PUBLICATION_PROFILE', raising=False)
    assert form_team_key('Home U19') != form_team_key('Home')


def test_result_binding_accepts_unique_alias_and_time_but_rejects_ambiguity(tmp_path):
    now=datetime(2026,11,10,12,tzinfo=UTC)
    f={'home':'js djidjel','away':'Away','kickoff':now.isoformat()}
    h=GoalHistory(tmp_path/'history.json')
    row={'home':'js jijel','away':'Away','kickoff':(now+timedelta(minutes=5)).isoformat()}
    h.data['fixture_ids']={'sstats:123':row}
    bind_source_ids(h,[f]);assert f['source_ids']['sstats']=='123'
    f.pop('source_ids');h.data['fixture_ids']['sstats:456']=row
    bind_source_ids(h,[f]);assert not f.get('source_ids')
    h.data['fixture_ids']={'sstats:123':{**row,'home':'js jijel U21'}}
    bind_source_ids(h,[f]);assert not f.get('source_ids')


def test_postponed_stays_pending_without_inferred_loss_or_void(tmp_path):
    now=datetime(2026,11,10,12,tzinfo=UTC)
    h=GoalHistory(tmp_path/'history.json')
    f={'version':'opponent-poisson-research-v1','home':'Home','away':'Away',
       'kickoff':(now-timedelta(days=1)).isoformat(),'created_at':(now-timedelta(days=2)).isoformat(),
       'p_over25':.5,'baseline_over25':.5,'status':'pending','provider_result_status':'Postponed'}
    h.data['forecasts']={'f':f}
    report=h.run_research([],now)
    assert f['status']=='pending' and 'total' not in f
    assert report['overdue_reasons']['provider_postponed']==1


def test_price_experiment_pending_results_also_enter_recovery(tmp_path):
    from app.services.research_result_recovery import overdue_forecasts
    now=datetime(2026,11,10,12,tzinfo=UTC)
    h=GoalHistory(tmp_path/'history.json')
    row={'id':'priced','home':'Home','away':'Away','kickoff':(now-timedelta(days=1)).isoformat(),'status':'pending'}
    h.data['priced_totals_trials']=[row,{**row,'id':'finished','status':'resolved'},
                                   {**row,'id':'future','kickoff':(now+timedelta(days=1)).isoformat()}]
    assert overdue_forecasts(h,now)==[row]
