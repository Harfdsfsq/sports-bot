from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.schemas import MatchContext
from app.services.daily_context_reliability import blend_contexts, evidence_quality, sample_support
from app.services.daily_quality_policy import quality_decision
from app.services.quality import PredictionQualityService


@pytest.fixture(autouse=True)
def daily(monkeypatch):
    monkeypatch.setenv('PUBLICATION_PROFILE', 'daily_quality')
    monkeypatch.setenv('DAILY_A_MIN_TEAM_SAMPLE', '6')
    monkeypatch.setenv('DAILY_A_MAX_GOAL_DISAGREEMENT', '.65')


def context(sample=None, source='sstats', home=1.4, away=1.2):
    return MatchContext(source, {}, expected_home=home, expected_away=away,
                        confidence=75, details={} if sample is None else
                        {'home_recent_count': sample, 'away_recent_count': sample})


def candidate(evidence):
    now = datetime.now(UTC)
    return SimpleNamespace(source_summary={'daily_context_reliability': evidence,
                           'context_sources_count': 1, 'context_source': 'sstats',
                           'daily_context_observed_at': now.isoformat(),
                           'daily_price_observed_at': now.isoformat()},
                           books_count=1, sources_count=1, confidence=75.,
                           ev_pct=10., edge_pct=5., adjusted_probability=.65, odds=1.9,
                           commence_time=now + timedelta(hours=1), family='totals',
                           expected_home=1.4, expected_away=1.2)


def blended(*contexts):
    return blend_contexts(list(contexts), lambda c: 1.)


def test_quality_does_not_increase_when_model_claims_more_value():
    c = candidate(blended(context(12)).details['daily_context_reliability'])
    service = PredictionQualityService(SimpleNamespace())
    first = service._candidate_quality_score(c, [], {}, False)
    c.ev_pct, c.edge_pct, c.confidence = 100, 50, 100
    assert service._candidate_quality_score(c, [], {}, False) == first
    assert first >= 78  # One well-supported context and one line can qualify.


@pytest.mark.parametrize('sample', [0, 1, 2])
def test_thin_sample_alone_cannot_pass_b(sample):
    merged = blended(context(sample))
    assert merged.confidence < 60
    c = candidate(merged.details['daily_context_reliability'])
    c.source_summary['quality_score'] = evidence_quality(c)
    assert c.source_summary['quality_score'] < 65
    assert 'below_b_quality' in quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=datetime.now(UTC))[1]


def test_unknown_sample_can_be_b_but_never_a_even_with_large_ev():
    merged = blended(context())
    c = candidate(merged.details['daily_context_reliability'])
    c.source_summary['quality_score'] = 100  # Defend against a stale legacy quality score.
    tier, reasons, _ = quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=datetime.now(UTC))
    assert tier == 'B' and not reasons
    assert evidence_quality(c) >= 65
    assert merged.confidence <= 69


def test_multiple_unknown_apis_do_not_create_a_supported_sample():
    merged = blended(context(), context(source='espn'))
    assert not merged.details['daily_context_reliability']['a_supported']
    assert merged.confidence <= 69


def test_flat_blend_is_order_independent_and_downweights_one_match_table():
    weak = MatchContext('openligadb', {}, expected_home=.3, expected_away=4., confidence=75,
                        details={'openligadb_table_home_matches': 1, 'openligadb_table_away_matches': 1})
    strong = context(12)
    extra = context(source='espn', home=1.5, away=1.1)
    a, b = blended(weak, strong, extra), blended(extra, strong, weak)
    assert a.expected_home == pytest.approx(b.expected_home)
    assert a.expected_away == pytest.approx(b.expected_away)
    assert a.expected_away < 1.7
    assert weak.expected_away == 4.  # Do not alter stored provider responses.


def test_disagreement_limits_a_without_forcing_two_apis():
    merged = blended(context(12, home=.3, away=.3), replace(context(12, home=4, away=4), source='sstats_form'))
    assert not merged.details['daily_context_reliability']['a_supported']
    c = candidate(merged.details['daily_context_reliability'])
    assert evidence_quality(c) < 78


@pytest.mark.parametrize('source,details,count', [
    ('football_data', {'football_data_home_sample': 8, 'football_data_away_sample': 6}, 6),
    ('football_data', {'football_data_home_played': 1, 'football_data_away_played': 2}, 1),
    ('thesportsdb', {'thesportsdb_home_played': 10, 'thesportsdb_away_played': 8}, 8),
    ('openligadb', {'openligadb_history_home_sample': 6, 'openligadb_history_away_sample': 5}, 5),
    ('espn', {'home_recent_count': 20, 'away_recent_count': 20}, None),
    ('sstats', {'home_recent_count': float('nan'), 'away_recent_count': 12}, None),
])
def test_sample_counts_are_provider_specific_and_finite(source, details, count):
    assert sample_support(MatchContext(source, {}, details=details))['minimum'] == count


def test_learning_excludes_shadow_without_modifying_accounting():
    service = PredictionQualityService(SimpleNamespace())
    real = {'prediction_id': 'real', 'status': 'lost', 'adjusted_probability_pct': 60,
            'market_probability_pct': 50, 'stake_amount': 1, 'pnl': -1}
    shadow = {**real, 'prediction_id': 'shadow', 'tracking_mode': 'shadow', 'status': 'won', 'pnl': 100}
    report = service.build_quality_report([real, shadow])
    assert report['summary']['excluded_shadow_rows'] == 1
    assert report['summary']['settled_binary_bets'] == 1
    assert report['summary']['learning_population'] == 'published_only'
    assert shadow['pnl'] == 100
    paired = report['calibration']['paired_probability_scores']
    assert paired['count'] == 1
    assert paired['model']['brier'] == .36
    assert paired['market']['brier'] == .25


def test_push_is_not_binary_loss_and_three_outcome_score_uses_unconditional_probabilities():
    service = PredictionQualityService(SimpleNamespace())
    row = {'status': 'push', 'is_hit': False, 'adjusted_probability_pct': 60, 'model_push_probability': .2}
    assert service._binary_result(row) is None
    score = service._win_push_loss_scores([row, {**row, 'status': 'void'}])
    assert score['count'] == 1
    assert score['brier'] == pytest.approx(.48**2 + .8**2 + .32**2, abs=1e-5)
    assert score['log_loss'] == pytest.approx(1.60944)


def test_old_standings_without_sample_count_cannot_be_a_or_b_alone():
    old = context(source='football_data')
    old.details['football_data_mode'] = 'standings'
    merged = blended(old)
    assert merged.confidence < 60
    assert merged.details['daily_context_reliability']['all_thin']


def test_invalid_goal_values_do_not_enter_blend_or_create_supported_goal_evidence():
    bad = context(20, home=float('nan'), away='broken')
    merged = blended(bad, context(8))
    assert merged.expected_home == 1.4 and merged.expected_away == 1.2
    single = blended(bad)
    assert single.expected_home is None and single.expected_away is None
    assert not single.details['daily_context_reliability']['a_supported']


def test_sub_one_percent_is_not_interpreted_as_fraction_in_daily_ledger():
    service = PredictionQualityService(SimpleNamespace())
    assert service._probability({'adjusted_probability_pct': .8}) == .008


def test_factory_confidence_cannot_rise_from_claimed_ev_or_price_premium(monkeypatch):
    from app.config import Settings
    from app.schemas import Match, Offer
    from app.services.model import CandidateFactory
    for line in __import__('pathlib').Path('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            monkeypatch.setenv(key, value)
    factory = CandidateFactory(Settings(_env_file=None))
    now = datetime.now(UTC)
    m = Match('day_inventory', 'test', 'soccer', 'Premier League', 'Home', 'Away',
              now + timedelta(hours=1), 'home', 'away', 'premier')
    ctx = blended(context(12))
    kwargs = dict(match=m, family='totals', selection='Under', point=2.5,
                  offers=[Offer('odds_api_io', 'Bet365', 'totals', 'Under', 2., 2.5)],
                  market_prob=.5, reasons=[], expected_home=1.4, expected_away=1.2,
                  model_mode='xg_total', context=ctx)
    low = factory._candidate_from_bucket(**kwargs, model_prob=.55)
    high = factory._candidate_from_bucket(**kwargs, model_prob=.95)
    assert high.ev_pct > low.ev_pct
    assert high.confidence == low.confidence


def test_legacy_quality_formula_remains_outside_daily_profile(monkeypatch):
    c = candidate(blended(context(12)).details['daily_context_reliability'])
    c.league_name = 'Premier League'
    c.selection_key, c.point, c.selection = 'under', 2.5, 'Under'
    monkeypatch.setenv('PUBLICATION_PROFILE', 'legacy')
    service = PredictionQualityService(SimpleNamespace())
    first = service._candidate_quality_score(c, [], {}, False)
    c.ev_pct = 1
    assert service._candidate_quality_score(c, [], {}, False) < first


def test_cup_sample_weights_follow_dates_not_league_bucket(monkeypatch):
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider
    provider = SStatsContextProvider(Settings(_env_file=None))
    now = datetime.now(UTC)
    old = {'start': now - timedelta(days=30), 'league': 'Cup', 'goals_for': 5, 'goals_against': 0, 'home': True}
    new = {'start': now - timedelta(days=2), 'league': '2. liga', 'goals_for': 0, 'goals_against': 2, 'home': True}
    rows = provider._select_recent_rows([new, old], league_name='Cup', before=now, limit=2)
    assert rows == [new, old]
    assert provider._weighted_average([r['goals_for'] for r in rows]) < 2.5
    monkeypatch.setenv('PUBLICATION_PROFILE', 'legacy')
    assert provider._select_recent_rows([new, old], league_name='Cup', before=now, limit=2) == [old, new]


def test_cached_form_reweight_preserves_raw_evidence_time_and_is_idempotent():
    import copy

    from app.providers.sstats import SStatsContextProvider
    now = datetime.now(UTC)
    old = {'start': (now-timedelta(days=30)).isoformat(), 'goals_for': 5, 'goals_against': 0, 'home': True}
    new = {'start': (now-timedelta(days=2)).isoformat(), 'goals_for': 0, 'goals_against': 2, 'home': True}
    ctx = MatchContext('sstats_form', {'home_recent': [old, new], 'away_recent': [old, new]},
                       expected_home=3, expected_away=3,
                       details={'daily_source_observed_at': (now-timedelta(hours=4)).isoformat()})
    original = copy.deepcopy(ctx)
    corrected = SStatsContextProvider.chronological_form_context(ctx)
    assert corrected.expected_home != ctx.expected_home
    assert corrected.payload['home_recent'][0] == new
    assert corrected.details['daily_source_observed_at'] == ctx.details['daily_source_observed_at']
    assert ctx == original
    assert SStatsContextProvider.chronological_form_context(corrected) is corrected


def queue_match(now, minutes=90, name='Home', league='Premier League'):
    from app.schemas import Match
    return Match('day_inventory', name, 'soccer', league, name, 'Away',
                 now + timedelta(minutes=minutes), name.lower(), 'away', 'league')


@pytest.mark.parametrize('result,elapsed,expected', [('empty', 29, False), ('empty', 30, True), ('unavailable', 60, False), ('rate_limited', 60, False), ('deferred', 60, False)])
def test_urgent_context_retry_only_overrides_short_empty_cooldown(tmp_path, result, elapsed, expected):
    from app.services.daily_match_registry import DailyMatchRegistry, match_identity
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = queue_match(now)
    registry.sync([m])
    at = now - timedelta(minutes=elapsed)
    registry.data['matches'][match_identity(m)]['attempts']['sstats:context'] = {
        'at': at.isoformat(), 'retry_after': (at + timedelta(minutes=120)).isoformat(), 'result': result}
    assert bool(registry.targets('sstats', 'context')) == expected


def test_pair_completion_outranks_optional_confirmation_and_bare_fixture(tmp_path):
    from app.schemas import MatchContext, Offer
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    bare, paired, confirmed = queue_match(now, 60, 'Bare'), queue_match(now, 120, 'Paired'), queue_match(now, 90, 'Confirmed')
    registry.sync([bare, paired, confirmed])
    registry.record('odds_api_io', 'offers', [paired], {paired.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Over', 2, 2.5)]}, {}, observed_at=now)
    ctx = MatchContext('sstats', {'HomeTeam': confirmed.home_team, 'AwayTeam': confirmed.away_team}, expected_home=1, expected_away=1)
    registry.record('sstats', 'context', [confirmed], {confirmed.match_key: ctx}, {}, observed_at=now)
    selected = registry.targets('other_sporting_api', 'context')
    assert [m.home_team for m in selected] == ['Paired', 'Bare', 'Confirmed']


def test_eligibility_filter_applies_before_provider_limit(tmp_path):
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    unsupported, supported = queue_match(now, 40, 'Unsupported'), queue_match(now, 90, 'Supported')
    registry.sync([unsupported, supported])
    assert registry.targets('espn', 'context', limit=1, eligible=lambda m: m.home_team == 'Supported')[0].home_team == 'Supported'


def test_budget_truncation_is_deferred_not_a_negative_data_cache(tmp_path):
    from app.services.daily_match_registry import DailyMatchRegistry, match_identity
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = queue_match(now)
    registry.sync([m])
    counts = registry.record('sstats', 'context', [m], {}, {'budget_exhausted': True}, observed_at=now)
    attempt = registry.data['matches'][match_identity(m)]['attempts']['sstats:context']
    assert attempt['result'] == 'deferred' and counts['deferred'] == 1
    assert datetime.fromisoformat(attempt['retry_after']) == now + timedelta(minutes=30)


def test_stale_provider_context_cannot_overwrite_valid_cached_evidence(tmp_path):
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = queue_match(now)
    registry.sync([m])
    good = MatchContext('sstats', {'HomeTeam': m.home_team, 'AwayTeam': m.away_team}, expected_home=1, expected_away=1)
    registry.record('sstats', 'context', [m], {m.match_key: good}, {}, observed_at=now)
    stale = replace(good, expected_home=4, details={'daily_source_observed_at': (now - timedelta(hours=7)).isoformat()})
    counts = registry.record('sstats', 'context', [m], {m.match_key: stale}, {}, observed_at=now)
    assert counts['rejected'] == 1 and counts['accepted'] == 0
    assert registry.cached('sstats', 'context', m).expected_home == 1
    assert registry.observation(m, 'context') == now.isoformat()


def test_native_batch_cap_does_not_mark_unassigned_matches_as_attempted(tmp_path, monkeypatch):
    import asyncio

    from app.config import Settings
    from app.services.daily_quality_runner import DailyQualityRunner
    monkeypatch.chdir(tmp_path)
    runner = DailyQualityRunner(Settings(_env_file=None, api_football_enabled=False))
    now = datetime.now(UTC)
    runner.registry.sync([queue_match(now, 60+i, f'Home{i}') for i in range(3)])
    runner.inventory_matches = runner.registry.matches()
    runner._provider_name = lambda p: 'api_football'
    class Limited:
        context_limit = 1
        api_key = 'test'
        async def fetch_context(self, matches):
            assert len(matches) == 1
            return {}, {'requests': 1}, {}
    _, stats, _ = asyncio.run(runner._fetch_provider(Limited(), 'fetch_context', [], empty_data={}))
    assert stats['assigned_matches'] == 1
    assert sum(bool(e['attempts']) for e in runner.registry.data['matches'].values()) == 1


def test_missing_api_key_does_not_call_provider_or_record_false_attempts(tmp_path, monkeypatch):
    import asyncio

    from app.config import Settings
    from app.services.daily_quality_runner import DailyQualityRunner
    monkeypatch.chdir(tmp_path)
    runner = DailyQualityRunner(Settings(_env_file=None, api_football_enabled=False))
    runner.registry.sync([queue_match(datetime.now(UTC))])
    runner.inventory_matches = runner.registry.matches()
    runner._provider_name = lambda p: 'api_football'
    class Missing:
        api_key = ''
        async def fetch_context(self, matches):
            pytest.fail('Missing-key provider called')
    _, stats, _ = asyncio.run(runner._fetch_provider(Missing(), 'fetch_context', [], empty_data={}))
    assert stats['assigned_matches'] == 0 and stats['reason'] == 'missing_api_key'
    assert not next(iter(runner.registry.data['matches'].values()))['attempts']


def test_espn_queries_only_target_league_and_target_utc_date(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from app.config import Settings
    from app.providers.espn import EspnContextProvider
    monkeypatch.chdir(tmp_path)
    for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    provider = EspnContextProvider(Settings(_env_file=None))
    m = queue_match(datetime(2026, 10, 14, 10, tzinfo=UTC))
    calls = []
    async def get(client, url, **kwargs):
        calls.append((url, kwargs['params']['dates']))
        return httpx.Response(200, json={'events': []}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    _, stats, _ = asyncio.run(provider.fetch_context([m]))
    assert len(calls) == 1
    assert '/eng.1/scoreboard' in calls[0][0]
    assert calls[0][1] == m.commence_time.strftime('%Y%m%d')
    assert stats['league_slugs_used'] == ['eng.1']


def test_verified_espn_event_venue_is_available_to_weather(tmp_path):
    from app.config import Settings
    from app.providers.weather_common import WeatherContextEnricher
    from app.services.daily_match_registry import DailyMatchRegistry, match_identity
    from app.services.daily_quality_runner import DailyQualityRunner
    now = datetime.now(UTC)
    m = queue_match(now)
    runner = DailyQualityRunner.__new__(DailyQualityRunner)
    runner.registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    runner.registry.sync([m])
    runner.inventory_matches = [m]
    ctx = MatchContext('espn', {'event': {'competitions': [{'venue': {'fullName': 'Neutral stadium', 'address': {'city': 'London', 'country': 'England'}}}]}})
    runner._retain_fixture_venues('espn', {m.match_key: ctx})
    assert m.metadata['venue_source'] == 'espn'
    assert runner.registry.data['matches'][match_identity(m)]['match']['metadata']['venue']['city'] == 'London'
    assert WeatherContextEnricher(Settings(_env_file=None))._location_from_fixture(m, {})['query'] == 'London, England'


def test_window_gap_counts_partition_real_matches(tmp_path):
    from app.schemas import Offer
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    bare, line, ctx, both = [queue_match(now, name=name) for name in ('Bare', 'Line', 'Context', 'Both')]
    registry.sync([bare, line, ctx, both])
    for m in (line, both):
        registry.record('odds_api_io', 'offers', [m], {m.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Over', 2, 2.5)]}, {}, observed_at=now)
    for m in (ctx, both):
        c = MatchContext('sstats', {'HomeTeam': m.home_team, 'AwayTeam': m.away_team}, expected_home=1, expected_away=1)
        registry.record('sstats', 'context', [m], {m.match_key: c}, {}, observed_at=now)
    coverage = registry.coverage()
    assert [coverage[key] for key in ('near_ready', 'near_only_line', 'near_only_context', 'near_missing_both')] == [1, 1, 1, 1]
    assert coverage['near'] == 4
    from scripts.send_daily_quality_report import render
    assert 'Добор окна: только линия — 1; только контекст — 1; нет обоих — 1.' in render({'daily_quality': {'coverage': coverage}})


@pytest.mark.parametrize('name,field', [('sstats', 'sstats_api_key'), ('football_data', 'football_data_api_key')])
def test_settings_only_api_key_is_checked_without_exposing_credentials(name, field):
    from app.services.daily_quality_runner import DailyQualityRunner
    provider = SimpleNamespace(settings=SimpleNamespace(**{field: None}))
    assert DailyQualityRunner._provider_missing_key(provider, name)
    setattr(provider.settings, field, 'test')
    assert not DailyQualityRunner._provider_missing_key(provider, name)


def test_overnight_espn_fixture_queries_previous_calendar_bucket():
    from app.config import Settings
    from app.providers.espn import EspnContextProvider
    provider = EspnContextProvider(Settings(_env_file=None))
    m = queue_match(datetime(2026, 10, 14, 0, tzinfo=UTC), minutes=90)
    assert provider._target_scoreboard_days([m], 'eng.1') == ['20261013', '20261014']


def test_fixture_before_next_regular_run_outranks_later_pair_completion(tmp_path):
    from app.schemas import Offer
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime.now(UTC)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    early, later = queue_match(now, 60, 'Early'), queue_match(now, 220, 'Later')
    registry.sync([early, later])
    registry.record('odds_api_io', 'offers', [later], {later.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Over', 2, 2.5)]}, {}, observed_at=now)
    assert registry.targets('sstats', 'context', limit=1)[0].home_team == 'Early'


def test_native_does_not_record_provider_subgroup_left_unqueried(tmp_path, monkeypatch):
    import asyncio

    from app.config import Settings
    from app.services.daily_quality_runner import DailyQualityRunner
    monkeypatch.chdir(tmp_path)
    runner = DailyQualityRunner(Settings(_env_file=None, api_football_enabled=False))
    now = datetime.now(UTC)
    runner.registry.sync([queue_match(now, 60+i, f'Home{i}') for i in range(3)])
    runner.inventory_matches = runner.registry.matches()
    runner._provider_name = lambda p: 'sstats'
    class Partial:
        async def fetch_context(self, matches):
            assert len(matches) == 3
            return {}, {'requests': 1, 'attempted_match_keys': [matches[0].match_key]}, {}
    _, stats, _ = asyncio.run(runner._fetch_provider(Partial(), 'fetch_context', [], empty_data={}))
    assert stats['assigned_matches'] == 3 and stats['unattempted_matches'] == 2
    assert sum(bool(e['attempts']) for e in runner.registry.data['matches'].values()) == 1


@pytest.mark.parametrize('home,away,league,year,reason', [
    ('FC Levadia Tallinn', 'Nomme Kalju FC', 'Estonia - Esiliiga', 2026, 'fixture_squad_ambiguous'),
    ('FC Levadia Tallinn U21', 'Nomme Kalju FC U21', 'Estonia - Esiliiga', 2026, None),
    ('FC Levadia Tallinn U19', 'Nomme Kalju FC U21', 'Esiliiga', 2026, 'fixture_squad_conflict'),
    ('Tallinna FC Flora', 'Viimsi JK', 'Esiliiga', 2026, 'fixture_squad_ambiguous'),
    ('FC Nõmme United', 'FC Tallinn', 'Esiliiga', 2026, 'fixture_squad_ambiguous'),
    ('Viimsi JK', 'FC Tallinn', 'Esiliiga', 2026, None),
    ('FC Levadia Tallinn', 'Nomme Kalju FC', 'Meistriliiga', 2026, None),
    ('FC Levadia Tallinn', 'Nomme Kalju FC', 'Esiliiga', 2027, None),
])
def test_reviewed_competition_squad_guard_is_specific(home, away, league, year, reason):
    from app.services.fixture_identity import fixture_squad_reasons
    m = SimpleNamespace(home_team=home, away_team=away, league_name=league,
                        commence_time=datetime(year, 10, 14, 16, tzinfo=UTC))
    assert fixture_squad_reasons(m) == ([reason] if reason else [])


def test_ambiguous_fixture_cannot_admit_context_or_publish_even_with_high_quality():
    from app.services.context_admission import identity_reasons
    c = candidate(blended(context(12)).details['daily_context_reliability'])
    c.home_team, c.away_team, c.league_name = 'FC Levadia Tallinn', 'Nomme Kalju FC', 'Esiliiga'
    c.commence_time = datetime(2026, 10, 14, 16, tzinfo=UTC)
    c.source_summary['quality_score'] = 100
    now = c.commence_time - timedelta(hours=1)
    for key in ('daily_price_observed_at', 'daily_context_observed_at'):
        c.source_summary[key] = now.isoformat()
    assert identity_reasons('sstats', c, context(12)) == ['fixture_squad_ambiguous']
    assert 'fixture_squad_ambiguous' in quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=now)[1]


def test_form_history_keeps_adult_and_youth_buckets_separate():
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider, form_team_key
    from app.services.context_admission import identity_reasons
    now = datetime(2026, 10, 14, 16, tzinfo=UTC)
    adult = queue_match(now, name='FC Levadia Tallinn', league='Meistriliiga')
    adult.away_team = 'Nomme Kalju FC'
    youth = replace(adult, source_event_id='youth', home_team='FC Levadia Tallinn U21',
                    away_team='Nomme Kalju FC U21', league_name='Esiliiga',
                    home_team_norm='levadia u21', away_team_norm='kalju u21')
    assert form_team_key(adult.home_team) != form_team_key(youth.home_team)
    rows = []
    for day in range(1, 5):
        for m, score in [(adult, (4, 0)), (youth, (0, 2))]:
            rows.append({'HomeTeam': m.home_team, 'AwayTeam': m.away_team,
                         'League': m.league_name, 'Date': (now-timedelta(days=day)).isoformat(),
                         'HomeScore': score[0], 'AwayScore': score[1]})
    contexts = []
    for m, goals in [(adult, 4), (youth, 0)]:
        ctx = SStatsContextProvider(Settings(_env_file=None))._build_team_form_contexts(
            [m], rows, {'team_form_examples': []})[m.match_key]
        contexts.append(ctx)
        assert ctx.details['home_recent_count'] == 4
        assert all(r['team'] == m.home_team and r['goals_for'] == goals for r in ctx.payload['home_recent'])
        assert identity_reasons('sstats', m, ctx) == []
    assert contexts[0].expected_home > contexts[1].expected_home


def test_same_named_opponents_of_different_squads_are_not_conflicting_results():
    from app.providers.sstats import conflicting_history_indices
    now = datetime(2026, 10, 10, tzinfo=UTC)
    row = {'opponent': 'Nomme Kalju FC', 'league': 'Cup', 'start': now,
           'goals_for': 1, 'goals_against': 0}
    other = dict(row, opponent='Nomme Kalju FC U21', goals_for=3)
    assert conflicting_history_indices([row, other]) == set()
    assert conflicting_history_indices([row, dict(other, opponent=row['opponent'])]) == {0, 1}


def test_explicit_u21_fixture_still_rejects_cached_adult_history():
    from app.services.context_admission import identity_reasons
    now = datetime(2026, 10, 14, 16, tzinfo=UTC)
    m = SimpleNamespace(home_team='FC Levadia Tallinn U21', away_team='Nomme Kalju FC U21',
                        league_name='Esiliiga', commence_time=now)
    ctx = MatchContext('sstats_form', {
        'home_recent': [{'team': 'FC Levadia Tallinn', 'start': (now-timedelta(days=1)).isoformat()}],
        'away_recent': [{'team': 'Nomme Kalju FC', 'start': (now-timedelta(days=1)).isoformat()}]})
    assert identity_reasons('sstats', m, ctx) == ['context_team_identity_mismatch']


def test_confirmed_u21_incident_is_excluded_without_rewriting_financial_row():
    from app.services.prediction_audit import invalid_prediction_reasons
    row = {'prediction_id': 'soccer|levadia tallinn|nomme kalju|2026-10-14|teamTotals|under|home|3.5|2026-10-14T16:00:00+00:00',
           'stake': 2.5, 'status': 'pending', 'pnl': None}
    original = dict(row)
    assert invalid_prediction_reasons(row) == ['first_team_history_used_for_confirmed_esiliiga_u21_fixture']
    assert row == original


def test_bulk_history_provider_closes_entire_backlog_without_changing_other_queues(tmp_path, monkeypatch):
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime(2026, 10, 14, 0, tzinfo=UTC)
    monkeypatch.setenv('DAILY_PREFETCH_BACKLOG_MATCH_LIMIT', '2')
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    registry.sync([queue_match(now, minutes=600+i, name='Home '+str(i)) for i in range(10)])
    assert len(registry.targets('sstats', 'context', limit=300)) == 2
    assert len(registry.targets('sstats', 'context', limit=300, bulk=True)) == 10
    assert len(registry.targets('odds_api_io', 'offers', limit=300)) == 2


def test_sstats_reuses_overlapping_history_windows_and_keeps_oldest_timestamp(tmp_path, monkeypatch):
    import asyncio
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider
    monkeypatch.chdir(tmp_path)
    provider = SStatsContextProvider(Settings(_env_file=None))
    calls = []
    async def rows(client, start, end, stats):
        calls.append((start, end))
        return [{'id': start, 'date': start, 'home': 'Home', 'away': 'Away'}]
    monkeypatch.setattr(provider, '_fetch_rows_window', rows)
    asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-10', {}))
    original = provider._daily_source_observed_at
    stats = {}
    asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-11', stats))
    assert calls == [('2026-10-01', '2026-10-07'), ('2026-10-08', '2026-10-10'), ('2026-10-08', '2026-10-11')]
    assert stats['history_windows_cache_hits'] == 1
    assert provider._daily_source_observed_at == original


def test_sstats_incomplete_history_is_not_saved_as_complete(tmp_path, monkeypatch):
    import asyncio
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider
    monkeypatch.chdir(tmp_path)
    provider = SStatsContextProvider(Settings(_env_file=None))
    async def rows(client, start, end, stats):
        stats['budget_exhausted'] = True
        return [{'id': 1, 'date': start, 'home': 'Home', 'away': 'Away'}]
    monkeypatch.setattr(provider, '_fetch_rows_window', rows)
    asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-02', {}))
    assert not list(tmp_path.rglob('sstats-*.json'))


def test_sstats_date_queries_explicitly_use_utc(monkeypatch):
    import asyncio
    import httpx
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider
    provider = SStatsContextProvider(Settings(_env_file=None))
    async def request(client, url, *, params, stats):
        assert params['TimeZone'] == 0
        assert params['to'] == '2026-10-15'
        return httpx.Response(200, json={'data': []})
    monkeypatch.setattr(provider, '_request_with_retries', request)
    asyncio.run(provider._fetch_rows_window(None, '2026-10-14', '2026-10-14', {'requests': 0, 'payload_shapes': []}))


def test_thesportsdb_cached_response_does_not_spend_budget_or_refresh_timestamp(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from app.config import Settings
    from app.providers.thesportsdb import TheSportsDbContextProvider
    monkeypatch.chdir(tmp_path)
    provider = TheSportsDbContextProvider(Settings(_env_file=None))
    class Client:
        async def get(self, url, params):
            return httpx.Response(200, json={'table': [{'strTeam': 'Home'}]})
    stats = {'requests': 0, 'http_statuses': []}
    first = asyncio.run(provider._fetch_json(Client(), '/lookuptable.php', stats, params={'l': '4328'}))
    key = provider._response_key('/lookuptable.php', {'l': '4328'})
    observed = provider.response_observations[key]
    provider.max_http_requests = 0
    second = asyncio.run(provider._fetch_json(None, '/lookuptable.php', stats, params={'l': '4328'}))
    assert first == second and stats['requests'] == 1
    assert stats['response_cache_hits'] == 1
    assert provider.response_observations[key] == observed


def test_thesportsdb_aliases_share_one_table_request(monkeypatch):
    import asyncio
    from app.config import Settings
    from app.providers.thesportsdb import TheSportsDbContextProvider
    for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    provider = TheSportsDbContextProvider(Settings(_env_file=None, enable_thesportsdb_context=True))
    now = datetime.now(UTC)
    first = queue_match(now, league='Italy - Serie A')
    second = queue_match(now, name='Another', league='Italian Serie A')
    calls = []
    async def fetch(client, path, stats, params=None):
        calls.append((path, params))
        if path == '/all_leagues.php':
            return {'leagues': [{'strSport': 'Soccer', 'strLeague': 'Italian Serie A', 'idLeague': '4332'}]}
        return {'table': []}
    monkeypatch.setattr(provider, '_fetch_json', fetch)
    _, stats, _ = asyncio.run(provider.fetch_context([first, second]))
    assert len(calls) == 2
    assert stats['table_requests_deduplicated'] == 1


def test_thesportsdb_unknown_league_never_falls_back_to_similar_foreign_table():
    from app.config import Settings
    from app.providers.thesportsdb import TheSportsDbContextProvider
    provider = TheSportsDbContextProvider(Settings(_env_file=None))
    rows = [{'strSport': 'Soccer', 'strLeague': 'English League One', 'idLeague': '4396'}]
    assert provider._resolve_league_ids(['Algeria - Ligue 1'], rows) == {}


def test_sstats_uses_cached_windows_even_when_no_http_budget_remains(tmp_path, monkeypatch):
    import asyncio
    from app.config import Settings
    from app.providers.sstats import SStatsContextProvider
    from app.services.daily_match_registry import write_json
    monkeypatch.chdir(tmp_path)
    provider = SStatsContextProvider(Settings(_env_file=None))
    provider.max_http_requests = 1
    row = {'id': 1, 'date': '2026-10-08', 'home': 'Home', 'away': 'Away'}
    write_json(tmp_path/'.data/provider_cache/sstats-window-v4-2026-10-08-2026-10-10.json',
               {'observed_at': datetime.now(UTC).isoformat(), 'rows': [row]})
    stats = {'requests': 1}
    result = asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-10', stats))
    assert result == [row] and stats['history_windows_cache_hits'] == 1
    assert not list(tmp_path.rglob('sstats-history-*.json'))


def openliga_rows(now):
    return [{'matchID': i, 'matchDateTimeUTC': (now-timedelta(days=i)).isoformat(),
             'team1': {'teamName': 'Home'}, 'team2': {'teamName': 'Away'},
             'matchIsFinished': True,
             'matchResults': [{'resultTypeID': 2, 'pointsTeam1': i % 2, 'pointsTeam2': 0}]}
            for i in range(1, 4)]


def test_openliga_real_adapter_context_survives_registry_and_json_cache(tmp_path):
    from app.config import Settings
    from app.providers.openligadb import OpenLigaDbContextProvider
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    m = queue_match(now, league='Bundesliga')
    ctx, _, _ = OpenLigaDbContextProvider(Settings(_env_file=None))._build_context(
        m, openliga_rows(now), [], comp_key='bl1', season=2026)
    assert ctx is not None
    assert len(ctx.payload['history']['recent_home_evidence']) == 3
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    registry.sync([m])
    assert registry.record('openligadb', 'context', [m], {m.match_key: ctx}, {}, observed_at=now)['accepted'] == 1
    registry.save()
    restored = DailyMatchRegistry(tmp_path/'registry.json', now)
    assert restored.cached('openligadb', 'context', m) is not None


@pytest.mark.parametrize('recent', [[('2026-10-13', 2, 1)], [['2026-10-13', 2, 1]]])
def test_legacy_openliga_history_is_rejected_without_crashing(tmp_path, recent):
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    m = queue_match(now)
    ctx = MatchContext('openligadb', {'history': {'recent_home': recent, 'recent_away': recent}}, expected_home=1, expected_away=1)
    registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    registry.sync([m])
    counts = registry.record('openligadb', 'context', [m], {m.match_key: ctx}, {}, observed_at=now)
    assert counts['rejected'] == 1
    assert registry.data['issues'][-1]['reason'] == 'context_history_identity_missing'
    assert registry.cached('openligadb', 'context', m) is None


def test_bad_provider_payload_is_a_rejection_not_a_pipeline_exception():
    from app.services.context_admission import admit_context
    now = datetime.now(UTC)
    ctx = MatchContext('openligadb', {'history': 'unexpected'}, expected_home=1, expected_away=1)
    assert 'context_payload_invalid' in admit_context('openligadb', queue_match(now), ctx, now.isoformat(), now)


def test_openliga_history_excludes_other_squads_and_unfinished_games():
    from app.config import Settings
    from app.providers.openligadb import OpenLigaDbContextProvider
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    m = queue_match(now)
    rows = openliga_rows(now)
    rows[0]['team1']['teamName'] = 'Home U21'
    rows[1]['matchIsFinished'] = False
    result, _ = OpenLigaDbContextProvider(Settings(_env_file=None))._history_metrics(m, rows)
    assert result is None  # Only one verified finished home game remains.


def test_openliga_preserves_zero_goal_final_scores():
    from app.providers.openligadb import OpenLigaDbContextProvider
    assert OpenLigaDbContextProvider._extract_final_score({'matchResults': [{'resultTypeID': 2, 'pointsTeam1': 0, 'pointsTeam2': 0}]}) == (0, 0)


def test_thesportsdb_unsupported_leagues_do_not_exhaust_catalog_limit(monkeypatch):
    import asyncio
    from app.config import Settings
    from app.providers.thesportsdb import TheSportsDbContextProvider
    for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    provider = TheSportsDbContextProvider(Settings(_env_file=None, enable_thesportsdb_context=True, thesportsdb_max_leagues=1))
    now = datetime.now(UTC)
    unsupported = [queue_match(now, name='Unknown '+str(i), league='Unavailable '+str(i)) for i in range(5)]
    supported = queue_match(now, name='Supported', league='England - Championship')
    calls = []
    async def fetch(client, path, stats, params=None):
        calls.append((path, params))
        if path == '/all_leagues.php':
            return {'leagues': [{'strSport': 'Soccer', 'strLeague': 'English League Championship', 'idLeague': '4329'}]}
        return {'table': []}
    monkeypatch.setattr(provider, '_fetch_json', fetch)
    _, stats, _ = asyncio.run(provider.fetch_context(unsupported+[supported]))
    assert calls[-1] == ('/lookuptable.php', {'l': '4329'})
    assert stats['unsupported_catalog_matches'] == 5
    assert stats['attempted_match_keys'] == [supported.match_key]


def test_conflicting_provider_features_do_not_depend_on_completion_order():
    a = context(8)
    a.details.update(home_form=.8, away_form=.2, home_ppg=2., away_ppg=1.)
    b = context(8, source='football_data')
    b.details.update(football_data_home_sample=8, football_data_away_sample=8,
                     home_form=.2, away_form=.8, home_ppg=1., away_ppg=2.)
    first, second = blended(a, b), blended(b, a)
    assert first == second
    assert 'home_form' not in first.details
    assert 'home_form' in first.details['daily_feature_conflicts']
    assert len(first.details['daily_provider_features']) == 2
    assert a.details['home_form'] == .8


def test_daily_goal_projection_is_not_increased_by_duplicate_features(monkeypatch):
    from app.services.model import CandidateFactory
    factory = CandidateFactory(SimpleNamespace(target_bookmakers=[], consensus_bookmakers=[]))
    c = context(8)
    c.details.update(home_form=.8, away_form=.2, home_ppg=2., away_ppg=1.,
                     home_rest_days=7., away_rest_days=3.)
    assert factory._enriched_expected_goals(None, c) == (1.4, 1.2)
    assert factory._adjust_total_probability(.6, 2.5, 1.4, 1.2, c) == .6
    assert factory._adjust_team_total_probability(.6, 1.4, 1.5, 'home', c) == .6
    monkeypatch.setenv('PUBLICATION_PROFILE', 'legacy')
    assert factory._enriched_expected_goals(None, c)[0] == pytest.approx(1.6526)


@pytest.mark.parametrize('point', [2., 2.5, 3., 3.5])
def test_daily_totals_derive_probability_and_refund_from_same_goal_model(point, monkeypatch):
    from collections import defaultdict
    from pathlib import Path
    from app.config import Settings
    from app.schemas import Match, Offer
    from app.services.model import CandidateFactory
    from app.services.daily_goal_probability import total_probability
    for line in Path('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            monkeypatch.setenv(key, value)
    factory = CandidateFactory(Settings(_env_file=None))
    now = datetime.now(UTC)
    m = Match('day_inventory', 'test', 'soccer', 'Premier League', 'Home', 'Away',
              now + timedelta(hours=1), 'home', 'away', 'premier')
    c = blended(context(12))
    # An API's unrelated direct total probability must not override score math.
    c.details.update(prob_over_2_5=.99, home_form=.95, away_form=.95,
                     home_gf_pg=3., away_gf_pg=3., home_ga_pg=3., away_ga_pg=3.)
    offers = [Offer('odds_api_io', book, 'totals', side, 2., point)
              for book in ['Bet365', 'Unibet'] for side in ['Over', 'Under']]
    candidates = factory._build_totals_candidates(m, offers, c, defaultdict(int))
    assert len(candidates) == 2
    distribution = total_probability(2.6, point)
    for candidate_row in candidates:
        expected = distribution.decisive_win if candidate_row.selection_key == 'over' else 1-distribution.decisive_win
        assert candidate_row.model_probability == pytest.approx(expected)
        assert candidate_row.source_summary['model_push_probability'] == pytest.approx(distribution.push)
        assert candidate_row.source_summary['goal_model_version'] == 'daily-goals-v4'


def test_squad_keys_distinguish_teams_and_preserve_adult_key():
    from app.utils import build_match_key
    now = datetime(2026, 10, 16, tzinfo=UTC)
    def key(home):
        return build_match_key('soccer', home, 'Nomme Kalju FC', now)
    adult = key('FC Levadia Tallinn')
    assert adult == 'soccer|levadia tallinn|nomme kalju|2026-10-16'
    assert len({adult, key('FC Levadia Tallinn U21'), key('FC Levadia Tallinn II'),
                key('FC Levadia Tallinn III'), key('FC Levadia Tallinn Women')}) == 5
    assert key('FC Levadia Tallinn U21') == key('FC Levadia Tallinn Under 21')


def test_registry_migration_keeps_publication_and_requeues_squad_evidence(tmp_path):
    import hashlib
    import json
    from dataclasses import asdict
    from app.schemas import Match
    from app.services.daily_match_registry import DailyMatchRegistry, match_identity
    from app.utils import canonicalize_team_name
    now = datetime(2026, 10, 16, 10, tzinfo=UTC)
    m = Match('sstats', 'u21', 'soccer', 'Esiliiga', 'FC Levadia Tallinn U21',
              'Nomme Kalju FC U21', now+timedelta(hours=1), 'levadia', 'kalju', 'esiliiga')
    old = '|'.join(['soccer', *sorted([canonicalize_team_name(m.home_team), canonicalize_team_name(m.away_team)]), m.commence_time.isoformat()])
    old_id = hashlib.sha256(old.encode()).hexdigest()[:24]
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps({'date_local': '2026-10-16', 'matches': {old_id: {
        'match': asdict(m), 'provider_ids': {}, 'evidence': {'old': {}}, 'attempts': {'old': {}}}},
        'published': [{'match_id': old_id, 'tier': 'B', 'at': now.isoformat()}], 'issues': []}, default=str))
    registry = DailyMatchRegistry(path, now)
    new_id = match_identity(m)
    assert new_id != old_id and old_id not in registry.data['matches']
    entry = registry.data['matches'][new_id]
    assert entry['publication']['match_id'] == new_id
    assert not entry['evidence'] and not entry['attempts']
    assert registry.data['published'][0]['match_id'] == new_id
    registry.save()
    assert DailyMatchRegistry(path, now).data == registry.data


def test_export_keeps_model_version_without_changing_settlement(tmp_path):
    from app.state import JsonStateStore
    state = JsonStateStore(str(tmp_path/'state.json'), str(tmp_path/'debug.json'))
    bet = {'status': 'won', 'odds': 2., 'stake_amount': 2.5,
           'settlement': {'pnl': 2.5, 'outcome': 'won'},
           'source_summary': {'goal_model_version': 'daily-goals-v2',
                              'context_feature_conflicts': ['home_form']}}
    row = state._accounting_row_for_bet(bet)
    assert row['goal_model_version'] == 'daily-goals-v2'
    assert row['context_feature_conflicts'] == ['home_form']
    assert bet['settlement']['pnl'] == 2.5


@pytest.mark.parametrize('name', ['H W Welders', 'H&W Welders', 'Harland And Wolff Welders'])
def test_club_initial_w_is_not_a_womens_squad(name):
    from app.services.fixture_identity import squad_identity, team_identity_key
    assert not squad_identity(name)[2]
    assert '#women' not in team_identity_key(name)


@pytest.mark.parametrize('name', ['Chelsea W', 'Chelsea (W)', 'Chelsea Women', 'Chelsea Ladies'])
def test_explicit_womens_suffix_still_distinguishes_squad(name):
    from app.services.fixture_identity import squad_identity
    assert squad_identity(name)[2]


def test_report_explains_only_remaining_candidate_already_published():
    from scripts.send_daily_quality_report import render
    summary = {'published_to_telegram': 0, 'candidates_raw': 1, 'skipped_already_in_state': 1,
               'daily_quality': {'published_today': [], 'coverage': {}}}
    report = render(summary)
    assert 'Прошедшие отбор ставки уже отправлены' in report
    assert 'Защита от повторов' in report
    summary['dry_run'] = True
    assert 'Проверка без отправки' in render(summary)
    summary['dry_run'] = False
    summary['daily_quality']['published_today'] = [{'tier': 'B'}]*5
    assert 'дневной лимит достигнут' in render(summary)


def test_sample_description_distinguishes_recent_form_and_season_table():
    from app.services.daily_context_reliability import sample_description
    form = MatchContext('sstats_form', {}, details={'home_recent_count': 4, 'away_recent_count': 5})
    table = MatchContext('football_data', {}, details={'football_data_mode': 'standings',
                          'football_data_home_played': 8, 'football_data_away_played': 8})
    f, t = sample_support(form), sample_support(table)
    assert f['sample_kind'] == 'recent_matches'
    assert t['sample_kind'] == 'season_table'
    description = sample_description({'sources': [f, t], 'best_team_sample': 8})
    assert 'форма SStats: 4/5' in description
    assert 'таблица сезона Football-data: 8/8' in description
    assert sample_description({'best_team_sample': 8}).startswith('выборка наиболее полного')


def test_calibration_synchronizes_artifact_explanation_and_keeps_previous_estimate():
    service = PredictionQualityService(SimpleNamespace())
    old_text = 'Модель до калибровки: 47.9%'
    c = SimpleNamespace(adjusted_probability=.47868, final_probability=.47868,
         market_probability=.38436, odds=2.55, confidence=70., publication_score=50.,
         reasons=[], source_summary={'model_push_probability': .21468,
         'adjusted_probability': .4787, 'analysis_points': [old_text, 'Другой контекст']},
         analysis={'sections': {'edge': old_text, 'recent': 'Контекст'},
                   'summary_points': [old_text, 'Другой контекст']})
    service._apply_probability_adjustment(c, -.05962)
    assert c.adjusted_probability == pytest.approx(.41906)
    assert c.source_summary['adjusted_probability'] == pytest.approx(.4191)
    assert c.source_summary['pre_calibration_adjusted_probability'] == .47868
    assert '41.9%' in c.analysis['sections']['edge']
    assert '47.9%' not in str(c.analysis)
    assert c.source_summary['analysis_points'] == c.analysis['summary_points']
    assert c.analysis['sections']['recent'] == 'Контекст'
    assert c.analysis['probability_gap_pp'] == c.source_summary['probability_gap_pp']


def test_repeated_season_tables_share_one_evidence_weight():
    def table(source):
        details={f'{source}_home_played':8,f'{source}_away_played':8,
                 f'{source}_home_gf_pg':3.2,f'{source}_home_ga_pg':.8,
                 f'{source}_away_gf_pg':3.,f'{source}_away_ga_pg':1.}
        if source=='football_data':details['football_data_mode']='standings'
        return MatchContext(source,{},expected_home=2.26,expected_away=1.9,
                            confidence=75,details=details)
    fd,ts=table('football_data'),table('thesportsdb')
    form=context(12,home=1.5,away=1.1)
    single=blended(fd,form);repeated=blended(fd,ts,form)
    assert repeated.expected_home==pytest.approx(single.expected_home)
    assert repeated.expected_away==pytest.approx(single.expected_away)
    evidence=repeated.details['daily_context_reliability']
    assert evidence['independent_evidence_count']==2
    assert evidence['duplicate_evidence_groups']==[['football_data','thesportsdb']]


@pytest.mark.parametrize('sample',[3,4,5])
def test_more_apis_cannot_promote_known_short_samples(sample):
    merged=blended(context(sample),context(source='espn'))
    evidence=merged.details['daily_context_reliability']
    assert evidence['insufficient_known_sample']
    assert merged.confidence<60
    c=candidate(evidence);c.source_summary['quality_score']=100
    tier,reasons,_=quality_decision(c,{'odds_sources_count':1,'context_sources_count':2,'books_count':3},now=datetime.now(UTC))
    assert 'insufficient_confirmed_sample' in reasons
    assert evidence_quality(c)<65


def test_model_version_cohorts_do_not_relabel_old_forecasts_or_remove_losses():
    service=PredictionQualityService(SimpleNamespace())
    def row(version,status,pnl):
        return {'goal_model_version':version,'status':status,'pnl':pnl,'stake_amount':1.,
                'adjusted_probability_pct':60.,'market_probability_pct':50.,'odds':2.,'family':'totals'}
    old=row('daily-goals-v2','lost',-1.);new=row('daily-goals-v3','won',1.)
    report=service.build_quality_report([old,new])
    assert report['summary']['count']==2 and report['summary']['pnl']==0
    cohorts=report['model_versions']
    assert cohorts['daily-goals-v2']['summary']['pnl']==-1.
    assert cohorts['daily-goals-v3']['summary']['pnl']==1.
    assert old['goal_model_version']=='daily-goals-v2'


@pytest.mark.parametrize('version_count,ready', [(0,False),(49,False),(50,True)])
def test_calibration_never_uses_another_goal_model_version(monkeypatch,version_count,ready):
    from datetime import UTC,datetime
    service=PredictionQualityService(SimpleNamespace(quality_min_history_bets=1,
        min_quality_score_publish=0,no_bet_quality_score_enabled=False))
    monkeypatch.setenv('DAILY_CALIBRATION_MIN_VERSION_BETS','50')
    c=candidate(blended(context(12)).details['daily_context_reliability'])
    c.source_summary.update(goal_model_version='daily-goals-v3')
    c.publication_score=50.;c.reasons=[];c.family='totals';c.selection_key='over'
    c.selection='Over';c.point=2.5;c.team_side=None;c.home_team='Home';c.away_team='Away'
    c.league_name='Premier League';c.match_key='match';c.model_mode='xg_total';c.diagnostics={};c.analysis={}
    captured=[]
    monkeypatch.setattr(service,'_candidate_calibration',lambda segments,profile,enough:captured.append((profile,enough)) or {'applied':False,'delta_probability':0.,'segments_used':[]})
    # Stop before unrelated publication guards; this test only verifies history routing.
    monkeypatch.setattr(service,'_context_sample_guard',lambda _: 'test_stop')
    report={'summary':{'settled_binary_bets':100},'profile':{'old_version':True},
            'model_versions':{'daily-goals-v2':{'summary':{'count':100},'profile':{'old_version':True}}}}
    if version_count:
        report['model_versions']['daily-goals-v3']={'summary':{'count':version_count},'profile':{'v3':True}}
    service.apply_to_candidates([c],report,datetime.now(UTC))
    assert captured==[({'v3':True} if version_count else {},ready)]
    assert c.source_summary['calibration_history']['binary_count']==version_count
    assert c.adjusted_probability==.65
