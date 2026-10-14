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
