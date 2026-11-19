from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import asyncio

import pytest

from app.schemas import Offer
from app.services.daily_price_confirmation import confirm
from app.services.totals_strategy_lab import (PROTOCOL, MODELS, paired_lines, evaluate_lines,
    distribution, fit_calibration, run_lab, resolve, summarize)

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def offers(point=2.5):
    return [Offer('odds_api_io', book, 'totals', side, price, point,
                  metadata={'fetched_at_utc': NOW.isoformat(), 'market_period': 'full_time'})
            for book in ('Bet365', 'Unibet') for side, price in [('Over', 1.9), ('Under', 1.9)]]


def candidate():
    return SimpleNamespace(family='totals', point=2.5, selection_key='over', bookmaker='Bet365',
        adjusted_probability=.6, odds=1.9, ev_pct=14., source_summary={'publication_tier': 'B', 'selected_source':'odds_api_io'})


@pytest.mark.parametrize('mutation', ['stale', 'half', 'different_book', 'different_line', 'lower_price'])
def test_exact_final_confirmation_blocks_invalid_quotes(mutation):
    quotes=offers(); c=candidate()
    for q in quotes:
        if mutation=='stale': q.metadata['fetched_at_utc']=(NOW-timedelta(minutes=1)).isoformat()
        if mutation=='half': q.metadata['market_period']='1h'
        if mutation=='different_book': q.bookmaker='Other'
        if mutation=='different_line': q.point=3.5
        if mutation=='lower_price': q.price=1.89
    assert confirm(c,quotes,NOW,NOW)[0] is False
    assert 'final_price_confirmation' not in c.source_summary


def test_final_confirmation_keeps_conservative_price_and_checks_ev():
    c=candidate(); q=offers()
    for o in q: o.price=2.
    assert confirm(c,q,NOW,NOW)[0]
    assert c.odds==1.9 and c.ev_pct==pytest.approx(14.)
    assert c.source_summary['final_price_confirmation']['available_price']==2
    c.adjusted_probability=.5
    assert confirm(c,q,NOW,NOW)==(False,'value_not_confirmed')


def test_paired_lines_never_mix_books_or_periods_or_old_prices():
    q=offers()
    assert len(paired_lines(q,NOW))==1
    assert paired_lines(q[:1]+q[3:],NOW)==[]  # opposite sides in different books
    assert paired_lines(q,NOW+timedelta(minutes=16))==[]
    assert paired_lines(q,NOW,{'betano'})==[]
    for o in q: o.metadata['market_period']='first half'
    assert paired_lines(q,NOW)==[]


def test_all_models_share_quotes_and_integer_push_conserves_probability():
    rates={'current_goals':3.3,'opponent':3.4,'shrunk':3.,'dispersion':.15}
    lines=paired_lines(offers(3.)+offers(2.5),NOW)
    evaluated,picks,anchor=evaluate_lines(lines,rates,{'alpha':0.,'intercept':0.})
    assert set(picks)==set(MODELS) and anchor['point']==2.5
    assert picks['calibrated']==picks['market']
    for line in evaluated:
        assert set(line['predictions'])==set(MODELS)
        for p in line['predictions'].values(): assert 0<p['over']<1 and 0<=p['push']<1
    for dispersion in (0.,.2):
        over,push=distribution(3.,3.,dispersion)
        assert over*(1-push)+push+(1-over)*(1-push)==pytest.approx(1)
        assert push>0


def calibration_rows(n=120):
    rows=[]
    for i in range(n):
        kickoff=NOW-timedelta(days=n-i)
        rows.append({'id':str(i),'protocol':PROTOCOL,'status':'resolved',
            'created_at':(kickoff-timedelta(hours=2)).isoformat(),'kickoff':kickoff.isoformat(),
            'resolved_at':(kickoff+timedelta(hours=3)).isoformat(),
            'anchor':{'point':2.5,'shrunk':.8 if i%2 else .2,'market':.5},'total':4 if i%2 else 1})
    return rows


def test_calibration_rejects_future_outcomes_and_is_not_fit_on_validation():
    rows=calibration_rows(); original=fit_calibration(rows,NOW)
    assert original['status']=='validated_correction' and original['alpha']>0
    future=deepcopy(rows)
    for r in future: r['resolved_at']=(NOW+timedelta(hours=1)).isoformat()
    assert fit_calibration(rows+future,NOW)==original
    assert fit_calibration(future,NOW)['count']==0
    # Contradict only the validation block: it must reject, not refit on it.
    changed=deepcopy(rows)
    for r in changed[-30:]: r['total']=1 if r['total']==4 else 4
    rejected=fit_calibration(changed,NOW)
    assert rejected['status']=='market_fallback' and rejected['alpha']==0
    assert fit_calibration(rows[:20],NOW)['status']=='insufficient_history'


def test_lab_freezes_first_prices_and_does_not_write_bankroll(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    match=SimpleNamespace(match_key='fixture',sport_key='soccer',home_team='Home',away_team='Away',league_name='League',commence_time=NOW+timedelta(hours=2))
    context=SimpleNamespace(expected_home=1.5,expected_away=1.5,
        details={'daily_context_reliability':{'best_team_sample':8}})
    class History:
        data={'results':{}}
        current_predictions={'fixture':{'opponent':3.3,'shrunk':3.1,'dispersion':.1}}
        def save(self): pass
    history=History(); registry=SimpleNamespace(cached=lambda *args:offers())
    result=run_lab(history,[match],{'fixture':context},registry,NOW)
    assert result['new']==1 and not result['automatic_live_promotion']
    frozen=deepcopy(history.data['priced_totals_trials'])
    context.expected_home=4.
    assert run_lab(history,[match],{'fixture':context},registry,NOW+timedelta(minutes=1))['new']==0
    assert history.data['priced_totals_trials']==frozen
    assert set(history.data)=={'results','priced_totals_trials','prospective_validation'}
    assert frozen[0]['validation_cohort_id']==history.data['prospective_validation']['cohort_id']


def test_conflicting_result_withdraws_evidence_and_push_is_zero_profit():
    row={'id':'one','protocol':PROTOCOL,'home':'Home','away':'Away','created_at':NOW.isoformat(),
         'kickoff':(NOW+timedelta(hours=2)).isoformat(),'status':'pending',
         'anchor':{'point':3.,**{name:.6 for name in MODELS}},
         'picks':{name:{'point':3.,'side':'over','odds':2.} for name in MODELS}}
    result={'home':'Home','away':'Away','kickoff':row['kickoff'],'hg':2,'ag':1,
            'observed_at':(NOW+timedelta(hours=5)).isoformat()}
    history=SimpleNamespace(data={'results':{'one':result}})
    resolve([row],history,NOW+timedelta(hours=6))
    summary=summarize([row])
    assert summary['settled']==1
    assert all(m['paper_pnl']==0 and m['paired_count']==0 for m in summary['models'].values())
    result['conflict']=True
    resolve([row],history,NOW+timedelta(hours=7))
    assert row['status']=='pending' and 'total' not in row and 'resolved_at' not in row


def test_publisher_does_not_send_failed_confirmation(monkeypatch):
    from app.services.daily_quality_runner import DailyTelegramPublisher
    from app.services.telegram import TelegramPublisher
    sent=[]
    async def send(self,bets,**kwargs): sent.extend(bets);return len(bets),[]
    monkeypatch.setattr(TelegramPublisher,'publish',send)
    publisher=DailyTelegramPublisher(SimpleNamespace(publish_dry_run=True))
    async def reject(c): return False
    publisher.confirm_price=reject
    bets=[candidate()]
    assert asyncio.run(publisher.publish(bets))[0]==0
    assert not bets and not sent


def test_price_experiment_clv_requires_same_book_and_before_kickoff(tmp_path, monkeypatch):
    from app.services import totals_strategy_clv as clv
    monkeypatch.chdir(tmp_path)
    kickoff=NOW+timedelta(minutes=30)
    row={'id':'fixture','match_key':'fixture','kickoff':kickoff.isoformat(),
         'created_at':(NOW-timedelta(hours=1)).isoformat(),
         'picks':{'shrunk':{'point':2.5,'side':'over','book':'bet365','odds':2.}}}
    clv.register([row],NOW)
    match=SimpleNamespace(match_key='fixture',commence_time=kickoff)
    quotes=offers()
    clv.update([match],{'fixture':quotes},NOW)
    measured=clv.update([],{},kickoff)
    assert measured['measured']==1 and measured['average_pct']==pytest.approx((2/1.9-1)*100)
    # Post-kickoff quotes cannot fill a missing closing reference.
    row['id']='second';clv.register([row],NOW)
    measured=clv.update([match],{'fixture':quotes},kickoff+timedelta(minutes=1))
    assert measured['measured']==1 and measured['missing']==1


def test_weighted_league_and_effective_sample_follow_same_decay():
    from app.services.goal_research import fit_model, result_row
    rows=[]
    for i in range(40):
        rows.append(result_row('League','A' if i%2 else 'B','B' if i%2 else 'A',
                    NOW-timedelta(days=500 if i<20 else i+1), 5 if i<20 else 1, 3 if i<20 else 1,'test'))
    model=fit_model(rows,NOW)
    assert model['weighted_league_total'] < model['league_total']
    assert all(model['effective_counts'][t] < model['counts'][t] for t in model['counts'])
    assert 0<=model['dispersion']<=.5


@pytest.mark.parametrize('case,settled', [('agree',True),('different_score',False),('provider_id_conflict',False),('different_time',False),('missing_ids',False),('future_observation',False),('wrong_squad',True)])
def test_consistent_identified_result_receipts_can_confirm_same_fixture(case,settled):
    from copy import deepcopy
    row={'home':'Chapecoense SC','away':'Mirassol FC SP','created_at':NOW.isoformat(),
         'kickoff':(NOW+timedelta(hours=2)).isoformat(),'status':'pending'}
    first={'home':'chapecoense','away':'mirassol','kickoff':row['kickoff'], 'hg':1,'ag':2,
           'observed_at':(NOW+timedelta(hours=5)).isoformat(),'provider_ids':{'sstats':'1492443'}}
    second={**deepcopy(first),'home':'chapecoense af','provider_ids':{'football_data':'555073'}}
    if case=='different_score':second['hg']=2
    if case=='provider_id_conflict':second['provider_ids']={'sstats':'different'}
    if case=='different_time':second['kickoff']=(NOW+timedelta(hours=2,minutes=1)).isoformat()
    if case=='missing_ids':second.pop('provider_ids')
    if case=='future_observation':second['observed_at']=(NOW+timedelta(days=1)).isoformat()
    if case=='wrong_squad':second['home']='Chapecoense U19'  # excluded, first receipt remains valid
    history=SimpleNamespace(data={'results':{'first':first,'second':second}})
    resolve([row],history,NOW+timedelta(hours=6))
    assert (row['status']=='resolved') is settled
    if settled:assert row['total']==3
    else:assert 'total' not in row and 'resolved_at' not in row


def test_invalidated_result_withdraws_outcome_without_changing_frozen_forecast():
    row={'home':'Home','away':'Away','created_at':NOW.isoformat(),
         'kickoff':(NOW+timedelta(hours=2)).isoformat(),'status':'pending',
         'rates':{'opponent':2.9},'anchor':{'point':2.5,'market':.5},
         'picks':{'opponent':{'side':'over','point':2.5,'odds':2.1}}}
    frozen=deepcopy({k:row[k] for k in ('rates','anchor','picks')})
    result={'home':'Home','away':'Away','kickoff':row['kickoff'],'hg':2,'ag':1,
            'observed_at':(NOW+timedelta(hours=5)).isoformat()}
    h=SimpleNamespace(data={'results':{'one':result}})
    resolve([row],h,NOW+timedelta(hours=6));assert row['status']=='resolved'
    result['observed_at']=(NOW+timedelta(days=1)).isoformat()
    resolve([row],h,NOW+timedelta(hours=6))
    assert row['status']=='pending' and 'total' not in row and 'resolved_at' not in row
    assert {k:row[k] for k in frozen}==frozen
