from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.schemas import Match, MatchContext, Offer
from app.services.daily_match_registry import DailyMatchRegistry, match_identity, write_json
from app.services.daily_quality_policy import quality_decision
from app.services.daily_quality_runner import DailyQualityRunner, DailyTelegramPublisher
from app.services.publication_thresholds import publish_floor, publish_min_context_sources, publish_min_odds_sources
from scripts.send_daily_quality_report import render


@pytest.fixture
def profile(monkeypatch):
    # CI has repository secrets; no regression test may use real credentials.
    for key in list(os.environ):
        if any(token in key for token in ('API_KEY', 'ODDS_API_IO_KEY', 'WEATHERAPI_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_BOT_TOKEN')):
            monkeypatch.delenv(key, raising=False)
    for line in Path(__file__).parents[1].joinpath('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            monkeypatch.setenv(key, value)
    monkeypatch.setenv('PUBLISH_DRY_RUN', 'true')
    monkeypatch.setenv('MAX_PICKS_PER_RUN', '2')


def match(now, minutes=90, name='Home'):
    return Match('day_inventory', 'event1', 'soccer', 'Premier League', name, 'Away', now + timedelta(minutes=minutes), name.lower(), 'away', 'premier', metadata={'day_inventory_source_ids': {'odds_api_io': '123', 'sstats': '456'}})


def candidate(now, quality=80, confidence=75, probability=.60):
    return SimpleNamespace(source_summary={'quality_score': quality, 'context_source': 'sstats', 'daily_price_observed_at': now.isoformat(), 'daily_context_observed_at': now.isoformat()}, commence_time=now + timedelta(minutes=90), adjusted_probability=probability, odds=1.9, confidence=confidence, family='totals', expected_home=1., expected_away=1.)


def test_one_source_is_valid_for_both_quality_tiers(profile):
    now = datetime.now(UTC)
    coverage = {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}
    assert quality_decision(candidate(now), coverage, now=now)[:2] == ('A', [])
    assert quality_decision(candidate(now, quality=70, confidence=65), coverage, now=now)[:2] == ('B', [])
    assert publish_floor() == publish_min_context_sources() == publish_min_odds_sources() == 1


@pytest.mark.parametrize('field,value,reason', [
    ('quality_score', 50, 'below_b_quality'), ('daily_price_observed_at', '2000-01-01T00:00:00+00:00', 'daily_price_observed_at_stale'),
    ('daily_context_observed_at', '', 'daily_context_observed_at_missing'), ('context_source', 'market_implied_xg', 'synthetic_context'),
])
def test_no_quality_or_stale_data_cannot_force_publication(profile, field, value, reason):
    now = datetime.now(UTC)
    c = candidate(now)
    c.source_summary[field] = value
    assert reason in quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=now)[1]


@pytest.mark.parametrize('minutes,passed', [(29, False), (30, True), (240, True), (241, False)])
def test_publication_window(minutes, passed):
    now = datetime.now(UTC)
    c = candidate(now)
    c.commence_time = now + timedelta(minutes=minutes)
    reasons = quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=now)[1]
    assert ('kickoff_outside_30m_4h' not in reasons) == passed


def test_exact_identity_preserves_kickoff_and_day(tmp_path):
    now = datetime.now(UTC).replace(hour=0)
    a = match(now)
    b = match(now, minutes=180)
    assert match_identity(a) != match_identity(b)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    r.sync([a, b])
    assert len(r.matches()) == 2
    assert r.matches()[0].metadata['provider_source_ids']['sstats'] == '456'
    assert DailyMatchRegistry(r.path, now + timedelta(days=1)).matches() == []


def test_queue_near_next_background_and_empty_backoff(tmp_path):
    now = datetime.now(UTC).replace(hour=0)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    items = [match(now, 800, 'Late'), match(now, 400, 'Next'), match(now, 45, 'Near'), match(now, 20, 'Soon')]
    r.sync(items)
    targets = r.targets('sstats', 'context')
    assert [m.home_team for m in targets] == ['Near', 'Next', 'Late']
    r.record('sstats', 'context', [targets[0]], {}, {}, observed_at=now)
    r.now = now + timedelta(minutes=10)
    assert all(m.home_team != 'Near' for m in r.targets('sstats', 'context'))


def test_cache_never_relabels_old_quote_as_fresh(tmp_path):
    now = datetime(2026, 10, 3, 0, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    m = match(now)
    r.sync([m])
    r.record('odds_api_io', 'offers', [m], {m.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Under', 1.9, 2.5)]}, {}, observed_at=now)
    stamp = r.observation(m, 'offers')
    assert stamp
    r.now += timedelta(minutes=16)
    assert r.cached('odds_api_io', 'offers', m) is None
    assert r.coverage()['collected_line'] == 1
    assert r.coverage()['line'] == 0


def test_registry_caps_inventory_at_300(tmp_path):
    now = datetime.now(UTC).replace(hour=0)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    r.sync([match(now, 600, f'Home{i}') for i in range(330)])
    assert len(r.matches()) == 300


def test_provider_id_conflicts_do_not_overwrite_verified_mapping(tmp_path):
    now = datetime.now(UTC)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    m = match(now)
    r.sync([m])
    m.metadata['day_inventory_source_ids']['sstats'] = 'different'
    r.sync([m])
    assert r.matches()[0].metadata['provider_source_ids']['sstats'] == '456'
    assert r.data['issues'][0]['kind'] == 'provider_id_conflict'


def test_daily_cap_and_smaller_b_limit(profile, tmp_path):
    now = datetime.now(UTC)
    runner = DailyQualityRunner.__new__(DailyQualityRunner)
    runner.settings = SimpleNamespace(max_picks_per_run=2)
    runner.registry = DailyMatchRegistry(tmp_path / 'registry.json', now)
    runner.registry.data['published'] = [{'tier': 'B'}] * 4
    runner.daily_rejections = {}
    a = SimpleNamespace(match_key='a', source_summary={'publication_tier': 'A', 'quality_score': 80}, ev_pct=6, commence_time=now + timedelta(hours=1), stake_amount=50, bankroll_snapshot=1000, stake_pct=0)
    b = SimpleNamespace(match_key='b', source_summary={'publication_tier': 'B', 'quality_score': 70}, ev_pct=4, commence_time=now + timedelta(hours=1), stake_amount=50, bankroll_snapshot=1000, stake_pct=0)
    assert runner._select_publishable_candidates([b, a]) == [a]
    assert a.stake_amount == 5
    runner.registry.data['published'] = []
    assert len(runner._select_publishable_candidates([b, a])) == 2
    assert b.stake_amount == 2.5


def test_report_counts_real_publications_not_service_messages(profile):
    text = render({'published_to_telegram': 0, 'telegram_messages_sent': 2, 'daily_quality': {'coverage': {'inventory': 300, 'near': 4, 'near_ready': 1}, 'published_today': [], 'selected': []}})
    assert 'не отправлено' in text
    assert 'A 0, B 0' in text
    assert 'покрыто частично' in text


def test_native_provider_fetch_assigns_real_context_targets(profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    runner = DailyQualityRunner(Settings(_env_file=None))
    m = match(now)
    runner.registry.sync([m])
    runner.inventory_matches = runner.registry.matches()
    runner._provider_name = lambda p: 'sstats'
    class Provider:
        async def fetch_context(self, targets):
            assert len(targets) == 1
            assert targets[0].metadata['provider_source_ids']['sstats'] == '456'
            return {targets[0].match_key: MatchContext('sstats', {}, expected_home=1, expected_away=.8, confidence=80)}, {'requests': 1}, {}
    data, stats, _ = asyncio.run(runner._fetch_provider(Provider(), 'fetch_context', [], empty_data={}))
    assert m.match_key in data
    assert stats['assigned_matches'] == 1
    assert runner.registry.cached('sstats', 'context', m) is not None
    assert runner.registry.coverage()['near_ready'] == 0  # Context alone is insufficient.


def test_complete_native_run_with_fake_apis(profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    m = match(now)
    # Use actual inventory loading, providers, model, quality, selection and dry publisher.
    row = asdict(m)
    row['source_ids'] = row['metadata']['day_inventory_source_ids']
    write_json(f'.data/day_inventory/{m.commence_time.astimezone(Settings().tzinfo).date()}.json', {'matches': [row]})
    runner = DailyQualityRunner(Settings(_env_file=None))
    class Odds:
        __module__ = 'app.providers.odds_api_io'
        async def fetch_offers(self, targets):
            return {t.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Under', 2.05, 2.5), Offer('odds_api_io', 'Bet365', 'totals', 'Over', 1.85, 2.5)] for t in targets}, {'requests': 1}, {}
    class Context:
        __module__ = 'app.providers.sstats'
        async def fetch_context(self, targets):
            return {t.match_key: MatchContext('sstats', {}, expected_home=.95, expected_away=.75, confidence=85, details={'team_form_index': 4}) for t in targets}, {'requests': 1}, {}
    for name in ('bzzoiro', 'football_data', 'thesportsdb', 'espn', 'openligadb', 'sportlogic', 'allsportsapi', 'bookies_api', 'oddspapi'):
        setattr(runner, name, None)
    runner.odds_api_io = Odds()
    runner.sstats = Context()
    summary = asyncio.run(runner.run_once())
    assert summary['matches_seen'] == 1
    assert summary['contexts_built'] == 1
    assert summary['daily_quality']['coverage']['near_ready'] == 1
    assert summary['published_to_telegram'] == 0
    assert summary['candidates_before_quality'] > 0
    assert summary['candidates_publishable'] > 0, (summary['rejections'], summary['daily_quality'])
    assert Path('.data/exports/latest-daily-provider-plan.json').exists()


def test_partial_delivery_only_marks_confirmed_forecasts(profile, monkeypatch):
    settings = Settings(_env_file=None, PUBLISH_DRY_RUN=False)
    publisher = DailyTelegramPublisher(settings)
    now = datetime.now(UTC)
    bets = [SimpleNamespace(commence_time=now + timedelta(hours=1), match_key='ok'), SimpleNamespace(commence_time=now + timedelta(hours=1), match_key='fail')]
    async def send(self, rows, **kwargs):
        return (1 if rows[0].match_key == 'ok' else 0), ['text']
    monkeypatch.setattr('app.services.telegram.TelegramPublisher.publish', send)
    count, _ = asyncio.run(publisher.publish(bets))
    assert count == len(bets) == 1
    assert bets[0].match_key == 'ok'


def test_pinned_odds_ids_skip_repeated_fixture_discovery(profile):
    from app.providers.odds_api_io import OddsApiIoProvider
    now = datetime.now(UTC)
    a, b = match(now), match(now, name='Another')
    mapping, missing = OddsApiIoProvider._registry_mapping([a])
    assert mapping[a.match_key]['event']['id'] == 123
    assert missing == []
    # The same provider ID cannot safely identify two different fixtures.
    assert OddsApiIoProvider._registry_mapping([a, b])[0] == {}


def test_sstats_history_cache_preserves_original_fetch_time(profile, tmp_path, monkeypatch):
    from app.providers.sstats import SStatsContextProvider
    monkeypatch.chdir(tmp_path)
    provider = SStatsContextProvider(Settings(_env_file=None))
    calls = []
    async def rows(client, start, end, stats):
        calls.append((start, end))
        return [{'id': 12, 'date': start, 'home': 'Home', 'away': 'Away'}]
    monkeypatch.setattr(provider, '_fetch_rows_window', rows)
    first = asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-02', {}))
    observed = provider._daily_source_observed_at
    second = asyncio.run(provider._fetch_rows(None, '2026-10-01', '2026-10-02', {}))
    assert first == second
    assert len(calls) == 1
    assert provider._daily_source_observed_at == observed


def test_diagnostics_hide_tokens_and_api_urls():
    from scripts.sanitize_daily_artifacts import sanitize
    text = 'https://api.telegram.org/bot123456:dummy-token/sendMessage ?apiKey=dummy-key&x=1'
    cleaned = sanitize(text, ['dummy-key'])
    assert 'dummy-token' not in cleaned
    assert 'dummy-key' not in cleaned


def test_midnight_keeps_prefetched_match_evidence(tmp_path):
    # 23:00 MSK: the four-hour window includes a match at 01:00 next day.
    now = datetime(2026, 10, 3, 20, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    m = match(now, minutes=120)
    r.sync([m])
    ctx = MatchContext('sstats', {}, expected_home=1, expected_away=1)
    r.record('sstats', 'context', [m], {m.match_key: ctx}, {}, observed_at=now)
    next_run = DailyMatchRegistry(r.path, now + timedelta(hours=1))
    assert len(next_run.matches()) == 1
    assert next_run.cached('sstats', 'context', m) is not None


def test_missing_summary_report_fails_without_reusing_old_run(profile, tmp_path, monkeypatch):
    from scripts import send_daily_quality_report
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    monkeypatch.setenv('RUN_STARTED_AT_UTC', now.isoformat())
    write_json('.data/exports/latest-run-summary.json', {'started_time_utc': (now - timedelta(days=1)).isoformat(), 'daily_quality': {}})
    monkeypatch.setattr(send_daily_quality_report, 'send_telegram', lambda *a: pytest.fail('Dry report attempted send'))
    assert send_daily_quality_report.main() == 1


def test_delivery_error_keeps_first_acknowledgement(profile, monkeypatch):
    publisher = DailyTelegramPublisher(Settings(_env_file=None, PUBLISH_DRY_RUN=False))
    now = datetime.now(UTC)
    bets = [SimpleNamespace(commence_time=now + timedelta(hours=1), match_key='ok'), SimpleNamespace(commence_time=now + timedelta(hours=1), match_key='fail')]
    acknowledged = []
    publisher.on_confirm = lambda bet: acknowledged.append(bet.match_key)
    async def send(self, rows, **kwargs):
        if rows[0].match_key == 'fail':
            raise RuntimeError('delivery failed')
        return 1, ['text']
    monkeypatch.setattr('app.services.telegram.TelegramPublisher.publish', send)
    count, _ = asyncio.run(publisher.publish(bets))
    assert count == len(bets) == 1
    assert acknowledged == ['ok']
    assert publisher.delivery_errors == ['RuntimeError']


def test_native_imports_do_not_install_legacy_patches(tmp_path):
    import subprocess
    import sys
    root = str(Path(__file__).parents[1])
    env = dict(os.environ, PUBLICATION_PROFILE='daily_quality', PYTHONPATH=root)
    code = "import sys; import app.providers; from app.services.daily_quality_runner import DailyQualityRunner; from scripts.sanitize_daily_artifacts import sanitize; assert 'app.services.strict_coverage_native_activation' not in sys.modules; assert 'app.services.autonomous_accumulation_persistence' not in sys.modules; assert 'app.cli' not in sys.modules"
    subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env, check=True, capture_output=True)
    assert not (tmp_path / '.data/exports').exists()


def test_night_coverage_counts_real_context_form_and_weather(tmp_path):
    now = datetime(2026, 10, 3, 20, 13, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path / 'registry.json', now)
    m = match(now, 90)
    r.sync([m])
    r.record('odds_api_io', 'offers', [m], {m.match_key: [Offer('odds_api_io', 'Bet365', 'totals', 'Under', 1.9, 2.5)]}, {}, observed_at=now)
    weather = MatchContext('weather', {}, details={'weather_context_applied': True})
    r.record('weather', 'context', [m], {m.match_key: weather}, {}, observed_at=now)
    assert not r.has_role(m, 'context')
    assert r.observation(m, 'context') is None
    ctx = MatchContext('sstats_form', {}, expected_home=1, expected_away=1, details={'home_recent_count': 7, 'away_recent_count': 8})
    r.record('sstats', 'context', [m], {m.match_key: ctx}, {}, observed_at=now)
    c = r.coverage()
    assert c['inventory'] == 0 and c['lookahead'] == 1
    assert c['near_ready'] == c['near_line'] == c['near_context'] == 1
    assert c['form'] == c['weather'] == 1


def test_daily_model_book_floor_and_family_policy(profile):
    from app.services.coverage_planner import CoveragePlanner
    from app.services.model import CandidateFactory
    settings = Settings(_env_file=None)
    assert CandidateFactory(settings)._required_publish_books(SimpleNamespace()) == 1
    assert CandidateFactory(settings)._required_books_for_bucket("teamTotals", 1.5, [], None) == 1
    assert CoveragePlanner(settings).min_books == 1
    now = datetime.now(UTC)
    c = candidate(now)
    c.family = 'h2h'
    assert 'market_outside_daily_policy' in quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=now)[1]


def test_report_groups_reasons_and_explains_midnight(profile):
    text = render({'current_time_local': '2026-10-03T23:15:00+03:00', 'dry_run': True, 'rejections': {'edge_below_threshold': 32, 'ev_below_threshold': 10, 'unsupported_total_line': 56}, 'daily_quality': {'coverage': {'inventory': 28, 'lookahead': 60, 'near': 60, 'near_ready': 27, 'near_line': 31, 'near_context': 34, 'near_missing_line': 29, 'near_missing_context': 26, 'form': 34, 'weather': 30}}})
    assert 'ценность ниже порога: 42' in text
    assert 'после полуночи' in text and '60' in text
    assert 'форма 34' in text and 'нет контекста у 26' in text


def test_bzzoiro_price_only_event_is_not_sporting_context(profile):
    from app.providers.bzzoiro import BzzoiroContextProvider
    provider = BzzoiroContextProvider(Settings(_env_file=None))
    assert provider._event_to_context({'id': 123, 'odds_home': 1.8, 'odds_away': 4, 'odds_draw': 3.2, 'odds_over_25': 1.9, 'odds_under_25': 1.9}, 'exact') is None


def test_handicap_probability_depends_on_line_and_selected_team():
    from app.services.daily_goal_probability import spread_probability
    easy = spread_probability(2.874, .482, -.5, 'home')
    hard = spread_probability(2.874, .482, -4.5, 'home')
    opposite = spread_probability(2.874, .482, 4.5, 'away')
    assert hard.decisive_win < .15 < easy.decisive_win
    assert hard.win + opposite.win == pytest.approx(1, abs=1e-10)
    assert hard.push == 0
    # Equal teams with no handicap: draw is refunded, decisive sides have equal probability.
    zero = spread_probability(1.2, 1.2, 0, 'home')
    assert zero.decisive_win == pytest.approx(.5) and zero.push > 0


def test_integer_total_does_not_count_refund_as_a_win():
    import math

    from app.services.daily_goal_probability import expected_value, total_probability
    row = total_probability(2, 2)
    # Independent closed-form reference: under 2 wins only at zero or one goal.
    assert row.loss == pytest.approx(3 * math.exp(-2))
    assert row.push == pytest.approx(2 * math.exp(-2))
    assert row.win + row.push + row.loss == pytest.approx(1)
    assert 1 - row.decisive_win == pytest.approx(row.loss / (1 - row.push))
    assert expected_value(.6, 1.9, .2) == pytest.approx(11.2)


@pytest.mark.parametrize('point', [-1.75, 2.25, float('nan')])
def test_unsupported_goal_lines_rejected_before_scoring(point):
    from app.services.daily_goal_probability import spread_probability, total_probability
    with pytest.raises(ValueError):
        spread_probability(1, 1, point, 'home')
    with pytest.raises(ValueError):
        total_probability(2, point)


def test_handicap_candidates_use_actual_line_in_native_factory(profile):
    from collections import defaultdict

    from app.services.daily_goal_probability import spread_probability
    from app.services.model import CandidateFactory
    now = datetime.now(UTC)
    m = match(now)
    factory = CandidateFactory(Settings(_env_file=None))
    captured = []
    factory._enriched_expected_goals = lambda *args: (2.874, .482)
    factory._candidate_from_bucket = lambda **kwargs: captured.append(kwargs)
    offers = [Offer('odds_api_io', 'Bet365', 'spreads', 'Home', 2.0, point, team_side='home') for point in [-.5, -4.5, -1.75]]
    reasons = defaultdict(int)
    factory._build_spread_candidates(m, offers, MatchContext('sstats', {}), reasons)
    assert len(captured) == 2 and reasons['unsupported_spread_line'] == 1
    assert captured[1]['model_prob'] == pytest.approx(spread_probability(2.874, .482, -4.5, 'home').decisive_win)
    assert captured[1]['model_prob'] < captured[0]['model_prob']


def test_final_candidate_rejections_are_visible_even_with_large_raw_counts(profile):
    text = render({'rejections': {'market_outside_daily_policy': 112}, 'daily_quality': {'quality_review': [{'match': 'Ciervos — Halcones', 'selection': 'Меньше', 'point': 4., 'odds': 2., 'quality': 57.178, 'reasons': ['no_bet_quality_score_guard']}]}})
    assert 'Почему финальные кандидаты' in text
    assert 'качество ниже минимальных 65' in text and '57.2' in text


def test_final_policy_ev_accounts_for_refund_probability(profile):
    now = datetime.now(UTC)
    c = candidate(now, probability=.54)
    c.odds = 2.
    c.source_summary['model_push_probability'] = .8
    tier, reasons, diagnostic = quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=now)
    assert diagnostic['canonical_ev_pct'] == pytest.approx(1.6)
    assert 'below_b_quality' in reasons


def test_large_negative_handicap_has_negative_value_at_short_price(profile):
    from collections import defaultdict

    from app.services.model import CandidateFactory
    factory = CandidateFactory(Settings(_env_file=None))
    factory._enriched_expected_goals = lambda *args: (2.874, .482)
    m = match(datetime.now(UTC))
    offer = Offer('odds_api_io', 'Bet365', 'spreads', 'Home', 2.5, -4.5, team_side='home')
    bets = factory._build_spread_candidates(m, [offer], MatchContext('sstats', {}, confidence=80), defaultdict(int))
    assert len(bets) == 1
    assert bets[0].model_probability < .15
    assert bets[0].ev_pct < 0
    assert bets[0].source_summary['context_sources_count'] == 1


def test_calibration_preserves_refund_aware_expected_value(profile):
    from app.services.quality import PredictionQualityService
    c = SimpleNamespace(adjusted_probability=.6, odds=1.9, market_probability=.5, source_summary={'model_push_probability': .2}, confidence=75, publication_score=20, reasons=[])
    PredictionQualityService(Settings(_env_file=None))._apply_probability_adjustment(c, -.05)
    assert c.adjusted_probability == pytest.approx(.55)
    assert c.ev_pct == pytest.approx(3.6)


def test_sstats_alias_and_weather_are_not_extra_sporting_apis(profile):
    from app.services.model import CandidateFactory
    factory = CandidateFactory(Settings(_env_file=None))
    m = match(datetime.now(UTC))
    ctx = MatchContext('sstats_form', {}, expected_home=1, expected_away=1, confidence=80, details={'merged_sources': ['sstats', 'sstats_form', 'weather']})
    offer = Offer('odds_api_io', 'Bet365', 'totals', 'Under', 2., 2.5)
    c = factory._candidate_from_bucket(match=m, family='totals', selection='Under', point=2.5, offers=[offer], market_prob=.5, model_prob=.65, reasons=[], expected_home=1., expected_away=1., model_mode='xg_total', context=ctx)
    assert c.source_summary['context_sources'] == ['sstats']
    assert c.source_summary['context_sources_count'] == 1


def test_publication_contract_does_not_restore_sstats_alias_as_confirmation(profile):
    from app.services.coverage_contract import evaluate_publish_candidate, sync_candidate_publish_coverage
    c = SimpleNamespace(source_summary={'context_source': 'sstats_form', 'context_sources': ['sstats', 'sstats_form', 'weather'], 'context_sources_count': 3, 'sources': ['odds_api_io'], 'books': ['Bet365']}, books_count=1, sources_count=1)
    sync_candidate_publish_coverage(c, Settings(_env_file=None))
    assert c.source_summary['context_sources'] == ['sstats']
    assert c.source_summary['context_sources_count'] == 1
    c.source_summary = {'context_source': 'weather', 'context_sources': ['weather'], 'context_sources_count': 99, 'sources': ['odds_api_io'], 'books': ['Bet365']}
    decision = evaluate_publish_candidate(c, Settings(_env_file=None))
    assert not decision.passed
    assert decision.report['context_sources_count'] == 0


def miami_candidate(now):
    from app.schemas import CandidateBet
    return CandidateBet(match_key='miami', sport_key='soccer', league_name='USL Championship', home_team='Tampa Bay Rowdies', away_team='Miami FC', commence_time=now + timedelta(hours=2), family='spreads', selection='Miami FC', selection_key='away', odds=2.75, fair_odds=1/.43, implied_probability=1/2.75, market_probability=1/2.75, consensus_probability=1/2.75, model_probability=.682, final_probability=.43, adjusted_probability=.43, edge_pct=6.6, ev_pct=18.2, confidence=62.1, books_count=1, sources_count=1, model_mode='xg_spread', point=.5, team_side='away', expected_home=1.29, expected_away=1.54, source_summary={'publication_tier': 'B', 'quality_score': 75.8, 'context_sources_count': 1, 'odds_sources_count': 1, 'model_push_probability': 0.})


def test_half_goal_forecast_shows_one_tier_and_no_refund(profile):
    from app.services.telegram import TelegramPublisher
    c = miami_candidate(datetime.now(UTC))
    text = TelegramPublisher(Settings(_env_file=None)).render_message([c])
    assert 'B-tier' in text and 'Ф2(+0.5)' in text
    assert 'Профиль сигнала: C' not in text
    assert 'с учётом возврата' not in text
    assert 'приоритет — варианты с 2+' not in text
    assert 'спортивного контекста 1' in text
    assert '4 часа' in text


def test_restored_empty_odds_are_retried_once_near_kickoff(tmp_path):
    now = datetime(2026, 10, 4, 0, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = match(now, 100)
    r.sync([m])
    r.record('odds_api_io', 'offers', [m], {}, {}, observed_at=now)
    entry = r.data['matches'][match_identity(m)]
    # Cached attempts from the previous version used a two-hour backoff.
    entry['attempts']['odds_api_io:offers']['retry_after'] = (now + timedelta(hours=2)).isoformat()
    r.now = now + timedelta(minutes=29)
    assert r.targets('odds_api_io', 'offers') == []
    r.now = now + timedelta(minutes=30)
    assert r.targets('odds_api_io', 'offers')[0].match_key == m.match_key
    r.record('odds_api_io', 'offers', [m], {}, {}, observed_at=r.now)
    assert r.targets('odds_api_io', 'offers') == []


def test_auth_failure_is_not_retried_by_urgent_odds_rule(tmp_path):
    now = datetime(2026, 10, 4, 0, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = match(now, 100)
    r.sync([m])
    r.record('odds_api_io', 'offers', [m], {}, {'auth_error': True}, observed_at=now)
    r.now = now + timedelta(minutes=30)
    assert r.targets('odds_api_io', 'offers') == []


def test_report_next_day_not_yet_due_is_not_a_failure(profile):
    text = render({'daily_quality': {'next_day_inventory': 0}})
    assert 'будет подготовлен вечером' in text
    assert 'следующего дня: 0/300' not in text


def test_same_match_cannot_be_published_in_another_market_next_run(profile, tmp_path):
    from collections import Counter
    now = datetime.now(UTC)
    runner = DailyQualityRunner.__new__(DailyQualityRunner)
    runner.settings = SimpleNamespace(max_picks_per_run=2)
    runner.registry = DailyMatchRegistry(tmp_path/'registry.json', now)
    runner.registry.data['published'] = [{'match_id': 'miami-id', 'tier': 'B'}]
    runner.daily_rejections = Counter()
    c = miami_candidate(now)
    c.source_summary['registry_match_id'] = 'miami-id'
    c.stake_amount = 2.5
    c.bankroll_snapshot = 1000
    assert runner._select_publishable_candidates([c]) == []
    assert runner.daily_rejections['already_published_match'] == 1


def test_acknowledged_next_day_match_retains_marker_after_midnight(tmp_path):
    now = datetime(2026, 10, 3, 20, 30, tzinfo=UTC)
    r = DailyMatchRegistry(tmp_path/'registry.json', now)
    m = match(now, 120)
    r.sync([m])
    identity = match_identity(m)
    r.data['published'] = [{'match_id': identity, 'tier': 'B', 'at': now.isoformat()}]
    r.save()
    next_day = DailyMatchRegistry(r.path, datetime(2026, 10, 3, 21, 1, tzinfo=UTC))
    assert next_day.data['published'] == []  # Counters reflect actual sending day.
    assert next_day.data['matches'][identity]['publication']['match_id'] == identity


def test_handicap_market_pairs_opposite_signed_lines(profile):
    from app.services.model import CandidateFactory
    factory = CandidateFactory(Settings(_env_file=None))
    away = Offer('odds_api_io', 'Bet365', 'spreads', 'Miami FC', 2.75, .5, team_side='away')
    home = Offer('odds_api_io', 'Bet365', 'spreads', 'Tampa', 1.425, -.5, team_side='home')
    wrong_home = Offer('odds_api_io', 'Bet365', 'spreads', 'Tampa', 1.05, .5, team_side='home')
    p = factory._fair_market_probability_spreads([away], [home, away, wrong_home], away.selection, .5, 'away')
    expected = (1/2.75) / (1/2.75 + 1/1.425)
    assert p == pytest.approx(expected)
    opposite = factory._fair_market_probability_spreads([home], [home, away, wrong_home], home.selection, -.5, 'home')
    assert p + opposite == pytest.approx(1)
