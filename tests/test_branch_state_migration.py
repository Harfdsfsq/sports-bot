import json
from pathlib import Path
import pytest
from scripts.migrate_branch_state import finish


def put(root, name, data):
    p=root/name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


def test_migration_keeps_bank_publications_and_new_trials(tmp_path):
    old, new = tmp_path/'old', tmp_path/'new'
    put(old,'state.json',{'bets':[{'fingerprint':'sent'}], 'bankroll':{'current_balance':973.84}})
    put(old,'daily_quality/goal-history.json',{'priced_totals_trials':[{'id':'old','status':'resolved'}]})
    put(new,'daily_quality/goal-history.json',{'priced_totals_trials':[{'id':'new','status':'pending'}]})
    put(old,'daily_quality/registry.json',{'matches':{'a':{'publication':{'match_id':'a'}}},'published':[{'match_id':'a'}]})
    put(old,'daily_quality/api-budget.json',{'accounts':{'a':{'day':'2026-11-10','day_used':10,'hour_requests':[1]}},'ips':{}})
    put(new,'daily_quality/api-budget.json',{'accounts':{'a':{'day':'2026-11-10','day_used':2,'hour_requests':[2]}},'ips':{}})
    finish(old,new)
    assert json.loads((new/'state.json').read_text())['bankroll']['current_balance']==973.84
    assert len(json.loads((new/'daily_quality/goal-history.json').read_text())['priced_totals_trials'])==2
    assert json.loads((new/'daily_quality/api-budget.json').read_text())['accounts']['a']['day_used']==12
    with pytest.raises(RuntimeError,match='financial positions'):finish(old,new)


def test_missing_history_cannot_start_new_live_bank(tmp_path):
    with pytest.raises(RuntimeError,match='Source financial'):finish(tmp_path/'missing',tmp_path/'dest')


def test_reviewed_alias_duplicates_merge_only_exact_fixture(tmp_path):
    from dataclasses import replace
    from datetime import UTC,datetime,timedelta
    from app.schemas import Match
    from app.services.daily_match_registry import DailyMatchRegistry
    now=datetime(2026,11,10,10,tzinfo=UTC)
    m=Match('odds_api_io','1','soccer','Algeria','JS Djidjel','CR Beni Thour',now+timedelta(hours=2),'js djidjel','cr beni thour','algeria',metadata={'provider_source_ids':{'odds_api_io':'1'}})
    other=replace(m,source='sstats',source_event_id='2',home_team='JS Jijel',metadata={'provider_source_ids':{'sstats':'2'}})
    r=DailyMatchRegistry(tmp_path/'registry.json',now)
    r.sync([m,other])
    assert len(r.matches())==1
    assert r.matches()[0].metadata['provider_source_ids']=={'odds_api_io':'1','sstats':'2'}
    r.sync([replace(other,home_team='JS Jijel U21')])
    assert len(r.matches())==2
