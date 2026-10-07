from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services.api_budget import ApiBudget, BudgetedAsyncClient, request_identity


@pytest.fixture
def budget_env(monkeypatch, tmp_path):
    # CI secrets must never influence account identity or reach mock clients.
    for name in list(os.environ):
        if any(token in name for token in ('_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CHAT_ID')):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PUBLICATION_PROFILE', 'daily_quality')
    monkeypatch.setenv('API_BUDGET_STATE_PATH', str(tmp_path / 'budget.json'))
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    return ApiBudget()


def test_account_hour_limit_is_rolling_and_survives_process_restart(budget_env, monkeypatch):
    monkeypatch.setenv('ODDS_API_IO_ACCOUNT_HOURLY_LIMIT', '2')
    now = datetime(2026, 10, 7, 10, 59, tzinfo=UTC)
    for n in range(2):
        assert budget_env.reserve('odds_api_io', 'account1', 'first', now=now + timedelta(seconds=n))[0] == 'allowed'
    assert ApiBudget().reserve('odds_api_io', 'account1', 'first', now=now + timedelta(minutes=2))[0] == 'hour_quota'
    assert ApiBudget().reserve('odds_api_io', 'account2', 'second', now=now)[0] == 'allowed'
    assert ApiBudget().reserve('odds_api_io', 'account1', 'first', now=now + timedelta(hours=1))[0] == 'allowed'


def test_daily_quota_is_shared_by_all_pipeline_phases(budget_env, monkeypatch):
    monkeypatch.setenv('ODDS_API_IO_ACCOUNT_DAILY_LIMIT', '3')
    now = datetime(2026, 10, 7, 23, 59, tzinfo=UTC)
    for _ in range(3):
        assert ApiBudget().reserve('odds_api_io', 'account1', 'same', now=now)[0] == 'allowed'
    assert ApiBudget().reserve('odds_api_io', 'account1', 'same', now=now)[0] == 'day_quota'
    assert ApiBudget().reserve('odds_api_io', 'account1', 'same', now=now + timedelta(minutes=2))[0] == 'allowed'


def test_monthly_weather_budget(budget_env, monkeypatch):
    monkeypatch.setenv('API_BUDGET_WEATHERAPI_MONTH', '1')
    monkeypatch.setenv('API_BUDGET_WEATHERAPI_RPM', '0')
    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert budget_env.reserve('weatherapi', '', 'key', now=now)[0] == 'allowed'
    assert budget_env.reserve('weatherapi', '', 'key', now=now + timedelta(days=1))[0] == 'month_quota'
    assert budget_env.reserve('weatherapi', '', 'key', now=now.replace(month=11))[0] == 'allowed'


def test_ip_pacing_applies_across_keys(budget_env):
    now = datetime.now(UTC)
    assert budget_env.reserve('sstats', '', 'one', now=now)[0] == 'allowed'
    reason, delay = budget_env.reserve('sstats', '', 'two', now=now)
    assert reason == 'pace' and delay == 2


def test_atomic_reservations_do_not_overspend(budget_env, monkeypatch):
    monkeypatch.setenv('ODDS_API_IO_ACCOUNT_DAILY_LIMIT', '5')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ApiBudget().reserve('odds_api_io', 'account1', 'same')[0], range(20)))
    assert results.count('allowed') == 5
    assert json.loads(budget_env.path.read_text())['accounts']['odds_api_io:same']['day_used'] == 5


def test_retry_after_blocks_only_affected_account(budget_env):
    now = datetime.now(UTC)
    identity = ('odds_api_io', 'account1', 'one')
    budget_env.reserve(*identity, now=now)
    response = httpx.Response(429, headers={'Retry-After': '600'}, request=httpx.Request('GET', 'https://api.odds-api.io/v3/odds/multi'))
    budget_env.observe(identity, response, now=now)
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=1))[0] == 'cooldown'
    assert budget_env.reserve('odds_api_io', 'account2', 'two', now=now)[0] == 'allowed'
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=601))[0] == 'allowed'


def test_paid_competition_does_not_disable_free_competitions(budget_env):
    now = datetime.now(UTC)
    identity = ('football_data', '', 'one')
    path = '/v4/competitions/paid/standings'
    budget_env.reserve(*identity, now=now, endpoint=path)
    budget_env.observe(identity, httpx.Response(403, request=httpx.Request('GET', 'https://api.football-data.org' + path)), now=now)
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=10), endpoint=path)[0] == 'endpoint_cooldown'
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=10), endpoint='/v4/competitions/PL/standings')[0] == 'allowed'


def test_blocked_request_never_reaches_transport_and_key_is_not_stored(budget_env, monkeypatch):
    monkeypatch.setenv('ODDS_API_IO_ACCOUNT_DAILY_LIMIT', '1')
    calls = []
    async def run():
        async with BudgetedAsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request.url.path) or httpx.Response(200, json=[]))) as client:
            first = await client.get('https://api.odds-api.io/v3/events', params={'apiKey': 'private-test-key'})
            second = await client.get('https://api.odds-api.io/v3/odds/multi', params={'apiKey': 'private-test-key'})
            assert first.status_code == 200 and second.status_code == 429
            assert second.headers['X-Local-API-Budget'] == 'day_quota'
    asyncio.run(run())
    assert calls == ['/v3/events']
    assert 'private-test-key' not in budget_env.path.read_text()


def test_secondary_key_alias_and_duplicate_accounts(budget_env, monkeypatch):
    from app.providers.odds_api_io import OddsApiIoProvider
    monkeypatch.setenv('ODDS_API_IO_KEY2', 'second')
    assert request_identity(httpx.Request('GET', 'https://api.odds-api.io/v3/events?apiKey=second'))[1] == 'account2'
    settings = SimpleNamespace(odds_api_io_key='same', odds_api_io_key_2='same')
    assert len(OddsApiIoProvider(settings)._odds_accounts()) == 1


@pytest.mark.parametrize('payload', [
    {'bookmakers': ['Bet365', 'Unibet']}, {'selectedBookmakers': [{'name': 'Bet365'}, {'name': 'Unibet'}]},
    {'data': {'bookmakers': ['Bet365', 'Unibet']}}, {'selected_bookmakers': ['Bet365', 'Unibet']},
])
def test_selected_bookmaker_shapes(payload):
    from app.providers.odds_api_io import OddsApiIoProvider
    assert OddsApiIoProvider._selected_bookmakers(payload) == ['Bet365', 'Unibet']


def test_accounts_use_real_selected_books_and_cache_read_only(budget_env, monkeypatch):
    from app.providers.odds_api_io import OddsApiIoProvider
    provider = OddsApiIoProvider(SimpleNamespace(odds_api_io_key='first', odds_api_io_key_2='second', odds_api_io_per_run_max=20))
    calls = []
    async def get(self, url, **kwargs):
        calls.append((url, kwargs['params']['apiKey']))
        names = ['Bet365', 'Unibet'] if kwargs['params']['apiKey'] == 'first' else ['William Hill', 'Bwin']
        return httpx.Response(200, request=httpx.Request('GET', url), json={'bookmakers': names})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    stats = {'accounts': {'account1': {}, 'account2': {}}}
    async def run():
        async with httpx.AsyncClient() as client:
            accounts = await provider._prepare_accounts(client, provider._odds_accounts(), stats)
            assert accounts[1]['bookmakers'] == 'William Hill,Bwin'
            await provider._prepare_accounts(client, provider._odds_accounts(), stats)
    asyncio.run(run())
    assert len(calls) == 2 and all(url.endswith('/bookmakers/selected') for url, _ in calls)
    assert all('first' not in p.read_text() and 'second' not in p.read_text() for p in Path('.data/daily_quality').glob('odds-books-*.json'))


def test_rate_limited_odds_account_does_not_stop_other_account(budget_env, monkeypatch):
    from app.providers.odds_api_io import OddsApiIoProvider
    provider = OddsApiIoProvider(SimpleNamespace(odds_api_io_key='first', odds_api_io_key_2='second', odds_api_io_per_run_max=20))
    calls = []
    async def request(client, key, ids, books):
        calls.append(key)
        return httpx.Response(429 if key == 'first' else 200, request=httpx.Request('GET', 'https://api.odds-api.io/v3/odds/multi'), json=[] if key == 'first' else [{'id': ids[0]}])
    monkeypatch.setattr(provider, '_request_odds_multi', request)
    stats = {'accounts': {}, 'odds_requests': 0, 'response_errors': 0, 'odds_http_statuses': [], 'payload_shapes': []}
    async def run():
        first = await provider._fetch_odds_multi_chunk(None, 'first', [1], 'Bet365,Unibet', stats, account_name='account1')
        second = await provider._fetch_odds_multi_chunk(None, 'second', [1], 'William Hill,Bwin', stats, account_name='account2')
        third = await provider._fetch_odds_multi_chunk(None, 'first', [2], 'Bet365,Unibet', stats, account_name='account1')
        assert first == third == [] and second == [{'id': 1}]
    asyncio.run(run())
    assert calls == ['first', 'second']


def test_workflow_maps_both_keys_and_profile_contains_no_credentials():
    root = Path(__file__).resolve().parents[1]
    config = (root / 'config/daily_quality.env').read_text()
    assert 'ODDS_API_IO_KEY_2=' not in config and 'ODDS_API_IO_KEY2=' not in config
    workflow = (root / '.github/workflows/run-bot-rules.yml').read_text()
    assert 'ODDS_API_IO_KEY_2: ${{ secrets.ODDS_API_IO_KEY_2 || secrets.ODDS_API_IO_KEY2 }}' in workflow


def test_near_line_requires_refresh_even_if_far_cache_was_valid(budget_env):
    from app.schemas import Match, Offer
    from app.services.daily_match_registry import DailyMatchRegistry
    now = datetime(2026, 10, 7, 8, tzinfo=UTC)
    match = Match('test', '1', 'soccer', 'League', 'Home', 'Away', now + timedelta(hours=5), 'home', 'away', 'league')
    registry = DailyMatchRegistry(Path('registry.json'), now)
    registry.sync([match])
    offer = Offer('odds_api_io', 'Bet365', 'totals', 'Over', 2.0, 2.5)
    registry.record('odds_api_io', 'offers', [match], {match.match_key: [offer]}, {}, observed_at=now)
    registry.now = now + timedelta(minutes=30)
    assert registry.cached('odds_api_io', 'offers', match)
    registry.now = now + timedelta(hours=1, minutes=1)
    assert registry.cached('odds_api_io', 'offers', match) is None


def test_full_odds_fetch_keeps_second_account_after_first_429(budget_env, monkeypatch):
    from app.providers.odds_api_io import OddsApiIoProvider
    from app.schemas import Match, Offer
    settings = SimpleNamespace(odds_api_io_key='first', odds_api_io_key_2='second', odds_api_io_per_run_max=20, max_matches_for_odds_fetch=20)
    provider = OddsApiIoProvider(settings)
    matches = [Match('day_inventory', str(i), 'soccer', 'League', f'Home {i}', f'Away {i}', datetime.now(UTC) + timedelta(hours=2), f'home{i}', f'away{i}', 'league', metadata={'provider_source_ids': {'odds_api_io': str(i)}}) for i in range(1, 12)]
    async def get(self, url, **kwargs):
        key = kwargs['params']['apiKey']
        if url.endswith('/bookmakers/selected'):
            return httpx.Response(200, request=httpx.Request('GET', url), json={'bookmakers': ['Bet365', 'Unibet']})
        assert url.endswith('/odds/multi')
        ids = [int(n) for n in kwargs['params']['eventIds'].split(',')]
        return httpx.Response(429 if key == 'first' else 200, request=httpx.Request('GET', url), json=[] if key == 'first' else [{'id': n} for n in ids])
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    monkeypatch.setattr(provider, '_parse_event_odds', lambda payload, match: [Offer('odds_api_io', 'Bet365', 'totals', 'Under', 2.0, 2.5)])
    offers, stats, _ = asyncio.run(provider.fetch_offers(matches))
    assert len(offers) == 11
    assert stats['accounts']['account1']['odds_requests'] == 1
    assert stats['accounts']['account2']['odds_requests'] == 2
    assert not stats['rate_limited'] and stats['any_account_rate_limited']
    assert all(rows[0].metadata['odds_api_io_account'] == 'account2' for rows in offers.values())


def test_inventory_bzzoiro_reads_second_page_and_moscow_midnight(budget_env, monkeypatch):
    from app.config import Settings
    from scripts.build_day_inventory_core import fetch_bzzoiro
    monkeypatch.setenv('BZZOIRO_API_KEY', 'test')
    monkeypatch.setenv('DAY_INVENTORY_BZZOIRO_MAX_REQUESTS', '8')
    calls = []
    async def get(self, url, **kwargs):
        if '/api/predictions/' in url:
            return httpx.Response(200, request=httpx.Request('GET', url), json={'results': [], 'next': None})
        params = kwargs['params']
        calls.append(params)
        assert params['limit'] == 200 and params['date_from'] == '2026-10-06' and params['date_to'] == '2026-10-07'
        offset = params['offset']
        rows = [{'id': n, 'home_team': f'Home{n} City', 'away_team': f'Away{n} Town', 'league_name': 'League', 'event_date': '2026-10-06T22:00:00Z'} for n in range(offset, 200 if offset == 0 else 201)]
        return httpx.Response(200, request=httpx.Request('GET', url), json={'results': rows, 'next': 'next' if offset == 0 else None})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    matches, stats = asyncio.run(fetch_bzzoiro(Settings(_env_file=None, app_timezone='Europe/Moscow'), '2026-10-07'))
    assert len(matches) == 201 and stats['endpoints']['events_v2_rows'] == 201, (stats, calls)
    assert [p['offset'] for p in calls] == [0, 200]


def test_report_shows_second_account_state_without_credentials():
    from scripts.send_daily_quality_report import render
    summary = {'daily_quality': {}, 'source_stats': {'odds_api_io': {'configured_accounts': ['account1', 'account2'], 'accounts': {'account1': {'events_matched': 10}, 'account2': {'rate_limited': True}}}}}
    text = render(summary)
    assert 'Аккаунт 1: дал линии для 10 матчей' in text
    assert 'Аккаунт 2: ограничение квоты' in text


def test_bzzoiro_server_quota_overrides_local_estimate(budget_env):
    now = datetime.now(UTC)
    identity = ('bzzoiro', '', 'account')
    budget_env.reserve(*identity, now=now)
    budget_env.observe(identity, httpx.Response(200, headers={'RateLimit': '"football";r=1;t=3600'}, request=httpx.Request('GET', 'https://sports.bzzoiro.com/api/events/')), now=now)
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=1))[0] == 'allowed'
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=2))[0] == 'server_quota'
    assert budget_env.reserve(*identity, now=now + timedelta(hours=1, seconds=1))[0] == 'allowed'


def test_missing_or_malformed_bzzoiro_headers_do_not_disable_data(budget_env):
    now = datetime.now(UTC)
    identity = ('bzzoiro', '', 'account')
    budget_env.reserve(*identity, now=now)
    budget_env.observe(identity, httpx.Response(200, headers={'RateLimit': 'bad invalid header'}, request=httpx.Request('GET', 'https://sports.bzzoiro.com/api/events/')), now=now)
    assert budget_env.reserve(*identity, now=now + timedelta(seconds=1))[0] == 'allowed'


def test_odds_server_iso_reset_is_account_specific(budget_env):
    now = datetime.now(UTC)
    identity = ('odds_api_io', 'account1', 'first')
    budget_env.reserve(*identity, now=now)
    budget_env.observe(identity, httpx.Response(200, headers={'x-ratelimit-remaining': '0', 'x-ratelimit-reset': (now + timedelta(hours=1)).isoformat()}, request=httpx.Request('GET', 'https://api.odds-api.io/v3/events')), now=now)
    assert budget_env.reserve(*identity, now=now + timedelta(minutes=2))[0] == 'server_quota'
    assert budget_env.reserve('odds_api_io', 'account2', 'second', now=now)[0] == 'allowed'
    assert budget_env.reserve(*identity, now=now + timedelta(hours=1, seconds=1))[0] == 'allowed'


def test_workflow_prepare_exports_only_assignments(tmp_path):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/run-bot-rules.yml').read_text()
    block = workflow.split('      - name: Prepare isolated run\n', 1)[1].split('      - name:', 1)[0].split('        run: |\n', 1)[1]
    command = '\n'.join(line[10:] for line in block.splitlines())
    (tmp_path / 'config').mkdir()
    profile = (root / 'config/daily_quality.env').read_text()
    (tmp_path / 'config/daily_quality.env').write_text(profile + '\n  # indented comment\n\n')
    output = tmp_path / 'github-env'
    subprocess.run(['bash', '-e', '-c', command], cwd=tmp_path, env={**os.environ, 'GITHUB_ENV': str(output)}, check=True, capture_output=True)
    rows = output.read_text().splitlines()
    assert all('=' in row and not row.lstrip().startswith('#') for row in rows)
    values = dict(row.split('=', 1) for row in rows)
    assert values['API_BUDGET_ENABLED'] == 'true'
    assert values['ODDS_API_IO_BOOKMAKERS_ACCOUNT2'] == '1xbet,Betano'
    assert 'ODDS_API_IO_KEY_2' not in values


def test_recovery_preserves_old_bank_and_deduplicates_republication():
    from scripts.recover_daily_cache import merge_state
    old = {'bankroll': {'current_balance': 999.38, 'open_exposure': 10, 'bets_published': 5, 'total_staked': 15}, 'bets': [{'fingerprint': 'corinthians', 'status': 'pending', 'telegram_sent': True, 'stake_amount': 2.5}]}
    current = {'bets': [dict(old['bets'][0]), {'fingerprint': 'vitoria', 'status': 'pending', 'telegram_sent': True, 'stake_amount': 2.5}]}
    merged = merge_state(old, current)
    assert len(merged['bets']) == 2
    assert merged['bankroll']['current_balance'] == 999.38
    assert merged['bankroll']['open_exposure'] == 12.5
    assert merged['bankroll']['bets_published'] == 6


def test_recovery_rejects_missing_or_conflicting_settlement():
    from scripts.recover_daily_cache import merge_state
    old = {'bankroll': {}, 'bets': [{'fingerprint': 'same', 'status': 'pending'}]}
    with pytest.raises(ValueError):
        merge_state(old, {'bets': [{'fingerprint': 'same', 'status': 'won'}]})


def test_cache_recovery_keeps_new_data_and_old_publication_markers(tmp_path, monkeypatch):
    from app.services.daily_match_registry import read_json, write_json
    from scripts.recover_daily_cache import BACKUP, MARKER, finish
    monkeypatch.chdir(tmp_path)
    write_json('.data/state.json', {'bankroll': {'current_balance': 999.38}, 'bets': [{'fingerprint': 'old', 'status': 'pending', 'telegram_sent': True}]})
    write_json('.data/daily_quality/registry.json', {'date_local': '2026-10-07', 'matches': {'old': {'publication': {'match_id': 'old'}}}, 'published': [{'match_id': 'old', 'tier': 'A'}]})
    write_json(BACKUP / 'state.json', {'bets': []})
    write_json(BACKUP / 'daily_quality/registry.json', {'date_local': '2026-10-07', 'matches': {'new': {}}, 'published': [{'match_id': 'new', 'tier': 'B'}]})
    write_json(BACKUP / 'daily_quality/api-budget.json', {'account': 'current'})
    finish()
    registry = read_json('.data/daily_quality/registry.json', {})
    assert {r['match_id'] for r in registry['published']} == {'old', 'new'}
    assert registry['matches']['old']['publication']['match_id'] == 'old'
    assert read_json('.data/state.json', {})['bankroll']['current_balance'] == 999.38
    assert read_json('.data/daily_quality/api-budget.json', {})['account'] == 'current'
    assert MARKER.exists() and not BACKUP.exists()


def test_changed_bookmakers_refresh_old_metadata_cache(budget_env, monkeypatch):
    import hashlib

    from app.providers.odds_api_io import OddsApiIoProvider
    provider = OddsApiIoProvider(SimpleNamespace(odds_api_io_key='first', odds_api_io_key_2='second', odds_api_io_per_run_max=20))
    path = Path('.data/daily_quality') / ('odds-books-' + hashlib.sha256(b'second').hexdigest()[:16] + '.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'at': datetime.now(UTC).timestamp(), 'configured_bookmakers': 'Bet365,Unibet', 'payload': {'bookmakers': ['Betfair Exchange', 'Sbobet']}}))
    calls = []
    async def get(self, url, **kwargs):
        calls.append(kwargs['params']['apiKey'])
        return httpx.Response(200, request=httpx.Request('GET', url), json={'bookmakers': ['1xbet', 'Betano']})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    async def run():
        async with httpx.AsyncClient() as client:
            accounts = await provider._prepare_accounts(client, [{'name': 'account2', 'api_key': 'second', 'bookmakers': '1xbet,Betano'}], {'accounts': {'account2': {}}})
            assert accounts[0]['bookmakers'] == '1xbet,Betano'
    asyncio.run(run())
    assert calls == ['second']
    assert json.loads(path.read_text())['configured_bookmakers'] == '1xbet,Betano'


def test_old_bookmaker_denial_does_not_block_new_selection(budget_env):
    from app.services.api_budget import endpoint_identity
    identity = ('odds_api_io', 'account2', 'test')
    now = datetime.now(UTC)
    old = httpx.Request('GET', 'https://api.odds-api.io/v3/odds/multi?apiKey=private&bookmakers=Betfair%20Exchange,Sbobet')
    new = httpx.Request('GET', 'https://api.odds-api.io/v3/odds/multi?apiKey=private&bookmakers=1xbet,Betano')
    budget_env.reserve(*identity, now=now, endpoint=endpoint_identity(old))
    budget_env.observe(identity, httpx.Response(403, request=old), now=now)
    assert budget_env.reserve(*identity, now=now, endpoint=endpoint_identity(old))[0] == 'endpoint_cooldown'
    assert budget_env.reserve(*identity, now=now, endpoint=endpoint_identity(new))[0] == 'allowed'
    assert 'private' not in budget_env.path.read_text()
