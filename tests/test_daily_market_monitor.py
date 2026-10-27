from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from app.services.market_monitor import MarketMonitor
from app.schemas import Match, Offer


@pytest.fixture
def daily(monkeypatch):
    monkeypatch.setenv('PUBLICATION_PROFILE', 'daily_quality')


def monitor(tmp_path):
    return MarketMonitor(SimpleNamespace(storage_export_dir=str(tmp_path/'exports'),
        market_monitor_subdir='market-monitor', clv_resolve_grace_minutes=45,
        bookmaker_weight=lambda _: 1., source_weight=lambda _: 1.))


def row(kickoff):
    return {'fingerprint': 'published', 'tracked_at': (kickoff-timedelta(hours=3)).isoformat(),
            'commence_time': kickoff.isoformat(), 'match_key': 'fixture',
            'market_signal_key': 'line', 'entry_odds': 2., 'entry_bookmaker': 'bet365'}


def snapshot(kickoff, before=30, book='bet365', fixture_time=None):
    return {'created_at': (kickoff-timedelta(minutes=before)).isoformat(),
            'meta': {'fixture': {'commence_time': (fixture_time or kickoff).isoformat()}},
            'matches': {'fixture': {'line': {'best_price': 9., 'prices_by_book': {book: 1.8}}}}}


def test_clv_survives_restart_and_keeps_last_valid_same_book_quote(tmp_path, daily):
    kickoff = datetime(2026, 10, 18, 15, tzinfo=UTC)
    m = monitor(tmp_path)
    m._write_json(m.pending_clv_path, [row(kickoff)])
    m._update_pending_clv(kickoff-timedelta(minutes=30), snapshot(kickoff))
    assert len(json.loads(m.pending_clv_path.read_text())) == 1
    # A new instance represents the next restored workflow; no in-play quote is used.
    restarted = monitor(tmp_path)
    restarted._update_pending_clv(kickoff+timedelta(minutes=10), snapshot(kickoff, before=-10))
    result = restarted.resolved_clv_rows()
    assert len(result) == 1
    assert result[0]['open_clv_pct'] == pytest.approx((2/1.8-1)*100, abs=.001)
    assert result[0]['reference_observed_at'] == (kickoff-timedelta(minutes=30)).isoformat()
    assert result[0]['measurement_basis'] == 'same_book_pre_kickoff_snapshot'


@pytest.mark.parametrize('case', ['other_book', 'other_fixture', 'old_quote', 'entry_quote', 'in_play'])
def test_unmatched_stale_or_in_play_prices_never_become_clv(tmp_path, daily, case):
    kickoff = datetime(2026, 10, 18, 15, tzinfo=UTC)
    m = monitor(tmp_path);m._write_json(m.pending_clv_path, [row(kickoff)])
    kwargs = {'book': 'unibet'} if case=='other_book' else {'fixture_time': kickoff+timedelta(hours=1)} if case=='other_fixture' else {'before': 90} if case=='old_quote' else {'before': 180} if case=='entry_quote' else {'before': -1}
    snap = snapshot(kickoff, **kwargs)
    at = datetime.fromisoformat(snap['created_at'])
    m._update_pending_clv(at, snap)
    m._update_pending_clv(kickoff+timedelta(minutes=10), {'created_at': kickoff.isoformat(), 'matches': {}})
    assert m.resolved_clv_rows() == []
    diagnostic = json.loads(m.clv_results_path.read_text())[0]
    assert diagnostic['measurement_status'] == 'missing_recent_same_book_quote'
    assert diagnostic['open_clv_pct'] is None


def test_snapshot_preserves_prices_by_book(tmp_path, daily):
    m=monitor(tmp_path);kickoff=datetime(2026,10,18,15,tzinfo=UTC)
    match=Match('test','1','soccer','League','Home','Away',kickoff,'home','away','league')
    offers=[Offer('odds_api_io', b, 'totals', 'Over', price, 2.5)
            for b,price in [('Bet365',2.),('Unibet',2.2)]]
    s=m._build_snapshot([match],{match.match_key:offers},kickoff-timedelta(hours=1))
    market=next(iter(s['matches'][match.match_key].values()))
    assert market['prices_by_book']=={'bet365':2.,'unibet':2.2}


def test_existing_cache_paths_remain_compatible_and_cover_market_state(tmp_path, daily):
    text=Path('.github/workflows/run-bot-rules.yml').read_text()
    assert text.count('            .data/daily_quality\n') >= 2
    assert '.data/exports/market-monitor/pending-clv.json' not in text
    m = monitor(tmp_path)
    assert m.pending_clv_path.parent == tmp_path/'daily_quality'/'market-monitor'
    m._write_json(m.pending_clv_path, [{'tracked': True}])
    assert json.loads((m.base_dir/'pending-clv.json').read_text()) == [{'tracked': True}]


def test_recover_future_confirmed_entries_once_without_publishing(tmp_path,daily):
    kickoff=datetime(2026,10,18,15,tzinfo=UTC);m=monitor(tmp_path)
    bet={'home_team':'Home','away_team':'Away','match_key':'fixture','family':'totals',
         'selection':'Over','selection_key':'over','point':2.5,'odds':2.,'fair_odds':2.,
         'market_probability':.5,'adjusted_probability':.55,'commence_time':kickoff.isoformat(),
         'published_at':(kickoff-timedelta(hours=3)).isoformat(),'telegram_sent':True,
         'source_summary':{'market_signal_key':'line','selected_bookmaker':'Bet365','goal_model_version':'daily-goals-v2'}}
    now=kickoff-timedelta(hours=1)
    assert m.restore_published_tracking([bet,{**bet,'telegram_sent':False}],now)==1
    assert monitor(tmp_path).restore_published_tracking([bet],now)==0
    stored=json.loads(m.pending_clv_path.read_text())
    assert stored[0]['tracked_at']==bet['published_at']
    assert stored[0]['entry_bookmaker']=='bet365'


def test_integer_total_clv_identity_matches_financial_ledger(tmp_path,daily):
    from app.state import JsonStateStore
    kickoff=datetime(2026,10,18,15,tzinfo=UTC);m=monitor(tmp_path)
    c=SimpleNamespace(match_key='fixture',family='totals',selection='Over',selection_key='over',
                      point=3.,team_side=None,commence_time=kickoff)
    expected=JsonStateStore._fingerprint_from_candidate(c)
    assert m._candidate_fingerprint(c)==expected
    old={**row(kickoff),'family':'totals','selection':'Over','selection_key':'over','point':3.,'team_side':None}
    old['fingerprint']=expected.replace('|3|','|3.0|')
    m._write_json(m.pending_clv_path,[old])
    m._update_pending_clv(kickoff-timedelta(minutes=30),snapshot(kickoff))
    m._update_pending_clv(kickoff+timedelta(minutes=1),{'matches':{}})
    resolved=m.resolved_clv_rows()[0]
    assert resolved['fingerprint']==expected
    assert resolved['previous_fingerprint']==old['fingerprint']


def test_paper_price_measurements_never_enter_live_calibration(tmp_path,daily):
    from app.services.totals_trial import PROTOCOL
    kickoff=datetime(2026,10,22,19,tzinfo=UTC);m=monitor(tmp_path)
    bet={'home_team':'Home','away_team':'Away','match_key':'fixture','family':'totals',
         'selection':'Over','selection_key':'over','point':2.5,'odds':2.,'fair_odds':2.,
         'market_probability':.5,'adjusted_probability':.6,'commence_time':kickoff.isoformat(),
         'published_at':(kickoff-timedelta(hours=2)).isoformat(),'telegram_sent':False,
         'source_summary':{'validation_protocol':PROTOCOL,'reserve_only':True,
             'market_signal_key':'line','selected_bookmaker':'Bet365','goal_model_version':'v4'}}
    assert m.restore_trial_tracking([bet],kickoff-timedelta(hours=1))==1
    assert m.restore_trial_tracking([bet],kickoff-timedelta(hours=1))==0
    m._update_pending_clv(kickoff-timedelta(minutes=30),snapshot(kickoff))
    m._update_pending_clv(kickoff+timedelta(minutes=1),{'matches':{}})
    assert m.resolved_clv_rows()==[]
    assert m.trial_clv_summary()['measured']==1
    assert m.trial_clv_summary()['average_pct']==pytest.approx((2/1.8-1)*100,abs=.001)


def test_trial_tracking_rejects_postkickoff_and_nonprotocol_rows(tmp_path,daily):
    m=monitor(tmp_path);now=datetime.now(UTC)
    rows=[{'family':'totals','telegram_sent':False,'commence_time':(now-timedelta(hours=1)).isoformat(),
           'published_at':(now-timedelta(hours=3)).isoformat(),'source_summary':{'reserve_only':True}}]
    assert m.restore_trial_tracking(rows,now)==0


def test_cached_quote_time_cannot_become_fresh_closing_price(tmp_path,daily):
    m=monitor(tmp_path);kickoff=datetime(2026,10,22,19,tzinfo=UTC)
    m._write_json(m.pending_clv_path,[row(kickoff)])
    snap=snapshot(kickoff,before=30)
    snap['matches']['fixture']['line']['price_observed_at_by_book']={'bet365':(kickoff-timedelta(minutes=70)).isoformat()}
    m._update_pending_clv(kickoff-timedelta(minutes=30),snap)
    m._update_pending_clv(kickoff+timedelta(minutes=1),{'matches':{}})
    assert m.resolved_clv_rows()==[]
    saved=json.loads(m.clv_results_path.read_text())[0]
    assert saved['measurement_status']=='missing_recent_same_book_quote'
    assert saved['reference_observed_at']==(kickoff-timedelta(minutes=70)).isoformat()


def test_monitor_uses_quote_collection_time_after_runner_start(tmp_path,daily):
    m=monitor(tmp_path);now=datetime.now(UTC);kickoff=now+timedelta(minutes=35)
    started=now-timedelta(minutes=3);fetched=now-timedelta(seconds=1)
    match=Match('test','1','soccer','League','Home','Away',kickoff,'home','away','league')
    offer=Offer('odds_api_io','Bet365','totals','Over',1.8,2.5,metadata={'fetched_at_utc':fetched.isoformat()})
    signal=m._signal_key(offer) if hasattr(m,'_signal_key') else None
    snap=m._build_snapshot([match],{match.match_key:[offer]},fetched)
    key=next(iter(snap['matches'][match.match_key]))
    tracked={**row(kickoff),'match_key':match.match_key,'market_signal_key':key,
             'tracked_at':(started-timedelta(hours=1)).isoformat()}
    m._write_json(m.pending_clv_path,[tracked])
    m.build_signals([match],{match.match_key:[offer]},started)
    saved=json.loads(m.pending_clv_path.read_text())[0]
    assert saved['reference_observed_at']==fetched.isoformat()
    assert json.loads(m.latest_snapshot_path.read_text())['created_at']==fetched.isoformat()


def test_quote_clock_does_not_accept_actual_future_timestamp(tmp_path,daily):
    m=monitor(tmp_path);now=datetime.now(UTC);kickoff=now+timedelta(hours=1)
    match=Match('test','1','soccer','League','Home','Away',kickoff,'home','away','league')
    offer=Offer('odds_api_io','Bet365','totals','Over',1.8,2.5,metadata={'fetched_at_utc':(now+timedelta(minutes=10)).isoformat()})
    m.build_signals([match],{match.match_key:[offer]},now)
    assert json.loads(m.latest_snapshot_path.read_text())['created_at']==now.isoformat()


@pytest.mark.parametrize('layout', ['list', 'dict'])
def test_vendor_last_change_is_preserved_but_does_not_expire_stable_prices(tmp_path, daily, layout):
    from app.providers.odds_api_io import OddsApiIoProvider
    now=datetime.now(UTC); changed=(now-timedelta(days=2)).isoformat()
    match=Match('test','1','soccer','League','Home','Away',now+timedelta(minutes=30),'home','away','league')
    market={'name':'Totals','updatedAt':changed,'odds':[{'hdp':2.5,'over':1.9,'under':1.9}]}
    payload={'bookmakers':{'Bet365':[market] if layout=='list' else {'Totals':market}}}
    offers=OddsApiIoProvider(SimpleNamespace())._parse_event_odds(payload,match)
    assert len(offers)==2
    assert all(o.metadata['provider_market_updated_at']==changed for o in offers)
    assert all(datetime.fromisoformat(o.metadata['fetched_at_utc']) >= now for o in offers)
    m=monitor(tmp_path);snap=m._build_snapshot([match],{match.match_key:offers},datetime.now(UTC))
    market=next(iter(snap['matches'][match.match_key].values()))
    assert market['price_changed_at_by_book']['bet365']==changed
    assert market['price_observed_at_by_book']['bet365']!=changed
