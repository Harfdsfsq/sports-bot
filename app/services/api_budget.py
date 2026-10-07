"""Shared free-plan budgets for inventory, enrichment and settlement.

No keys or request URLs are persisted. Reservations count attempts, including
failed requests, and survive separate processes and restored Actions caches.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from http_sf import parse


@dataclass(frozen=True)
class Policy:
    rpm: int = 0
    hour: int = 0
    day: int = 0
    month: int = 0


# Zero means no published quota for that window, never an invented allowance.
POLICIES = {
    'odds_api_io': Policy(hour=100, day=500),
    'bzzoiro': Policy(rpm=600, day=7500),  # below the documented 25 requests/s burst
    'sstats': Policy(rpm=30),  # conservative public-IP limit
    'football_data': Policy(rpm=10),
    'thesportsdb': Policy(rpm=30),
    'api_football': Policy(rpm=10, day=100),
    'weatherapi': Policy(rpm=60, month=100000),
    'openweathermap': Policy(rpm=60, month=1000000),
    'openligadb': Policy(rpm=30),  # local politeness ceiling, not a vendor promise
    'clubelo': Policy(rpm=30),
    'espn': Policy(rpm=30),
}
HOSTS = {
    'api.odds-api.io': 'odds_api_io', 'sports.bzzoiro.com': 'bzzoiro',
    'api.sstats.net': 'sstats', 'api.football-data.org': 'football_data',
    'www.thesportsdb.com': 'thesportsdb', 'thesportsdb.com': 'thesportsdb',
    'v3.football.api-sports.io': 'api_football', 'api.weatherapi.com': 'weatherapi',
    'api.openweathermap.org': 'openweathermap', 'api.openligadb.de': 'openligadb',
    'api.clubelo.com': 'clubelo', 'site.api.espn.com': 'espn', 'sports.core.api.espn.com': 'espn',
}
_LOCK = threading.RLock()


def enabled():
    return os.getenv('PUBLICATION_PROFILE') == 'daily_quality' and os.getenv('API_BUDGET_ENABLED', 'true').lower() == 'true'


def policy_for(provider: str, account: str = '') -> Policy:
    default = POLICIES[provider]
    limits = {}
    for scope in ('rpm', 'hour', 'day', 'month'):
        names = [f'API_BUDGET_{provider.upper()}_{scope.upper()}']
        if provider == 'odds_api_io' and scope in {'hour', 'day'}:
            suffix = 'HOURLY_LIMIT' if scope == 'hour' else 'DAILY_LIMIT'
            names = [f'ODDS_API_IO_{account.upper()}_{suffix}', f'ODDS_API_IO_ACCOUNT_{suffix}', *names]
        value = next((os.getenv(n) for n in names if os.getenv(n)), None)
        limits[scope] = max(0, int(value)) if value is not None else getattr(default, scope)
    return Policy(**limits)


def request_identity(request: httpx.Request):
    provider = HOSTS.get(request.url.host)
    if provider is None:
        return None
    params = request.url.params
    credential = (params.get('apiKey') or params.get('apikey') or params.get('key') or params.get('appid')
                  or request.headers.get('Authorization') or request.headers.get('X-Auth-Token')
                  or request.headers.get('x-apisports-key') or request.headers.get('x-rapidapi-key') or '')
    if provider == 'thesportsdb':
        parts = request.url.path.split('/')
        credential = parts[4] if len(parts) > 4 else credential
    account = ''
    if provider == 'odds_api_io':
        secondary = os.getenv('ODDS_API_IO_KEY_2') or os.getenv('ODDS_API_IO_KEY2')
        account = 'account2' if secondary and credential == secondary else 'account1'
    # IP limits are shared by all keys; account quotas are fingerprinted.
    fingerprint = hashlib.sha256(credential.encode()).hexdigest()[:16] if credential else 'public'
    return provider, account, fingerprint


class ApiBudget:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv('API_BUDGET_STATE_PATH', '.data/daily_quality/api-budget.json'))

    @contextmanager
    def state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, self.path.with_suffix('.lock').open('a') as lock:
            try:
                import fcntl
            except ImportError:
                fcntl = None
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                payload = json.loads(self.path.read_text()) if self.path.exists() else {'accounts': {}, 'ips': {}}
                yield payload
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')
                temporary.replace(self.path)
            finally:
                if fcntl:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def reserve(self, provider, account, fingerprint, *, now=None, endpoint=''):
        now = now or datetime.now(UTC)
        timestamp = now.timestamp()
        policy = policy_for(provider, account)
        with self.state() as data:
            row = data['accounts'].setdefault(provider + ':' + fingerprint, {'provider': provider, 'account': account})
            for scope, bucket in [('day', now.strftime('%Y-%m-%d')), ('month', now.strftime('%Y-%m'))]:
                if row.get(scope) != bucket:
                    row[scope], row[scope + '_used'] = bucket, 0
            recent = row['hour_requests'] = [t for t in row.get('hour_requests', []) if timestamp - t < 3600]
            if row.get('blocked_until', 0) > timestamp:
                return 'cooldown', row['blocked_until'] - timestamp
            if row.get('server_reset_at', 0) > timestamp and row.get('server_remaining') == 0:
                row['last_block'] = 'server_quota'
                return 'server_quota', row['server_reset_at'] - timestamp
            endpoint_id = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
            if row.get('blocked_endpoints', {}).get(endpoint_id, 0) > timestamp:
                return 'endpoint_cooldown', row['blocked_endpoints'][endpoint_id] - timestamp
            for scope, used in [('hour', len(recent)), ('day', row['day_used']), ('month', row['month_used'])]:
                if getattr(policy, scope) and used >= getattr(policy, scope):
                    row['last_block'] = scope + '_quota'
                    return row['last_block'], 0
            last = data['ips'].get(provider, 0)
            delay = max(0, last + 60 / policy.rpm - timestamp) if policy.rpm else 0
            if delay:
                return 'pace', delay
            data['ips'][provider] = timestamp
            row['hour_requests'].append(timestamp)
            row['day_used'] += 1
            row['month_used'] += 1
            if row.get('server_reset_at', 0) > timestamp and 'server_remaining' in row:
                row['server_remaining'] = max(0, row['server_remaining'] - 1)
            row['limits'] = policy.__dict__
            row['last_request_at'] = now.isoformat()
            row.pop('last_block', None)
            return 'allowed', 0

    def observe(self, identity, response, *, now=None):
        provider, account, fingerprint = identity
        now = now or datetime.now(UTC)
        with self.state() as data:
            row = data['accounts'][provider + ':' + fingerprint]
            row['last_status'] = response.status_code
            if provider == 'odds_api_io' and response.headers.get('x-ratelimit-reset'):
                try:
                    reset = datetime.fromisoformat(response.headers['x-ratelimit-reset'].replace('Z', '+00:00'))
                    remaining = max(0, int(response.headers['x-ratelimit-remaining']))
                    if reset.tzinfo is None:
                        raise ValueError('reset must include timezone')
                    if row.get('server_reset_at', 0) > now.timestamp():
                        remaining = min(remaining, row.get('server_remaining', remaining))
                    row.update(server_remaining=remaining, server_reset_at=reset.timestamp())
                except (ValueError, KeyError, TypeError):
                    row['server_header_invalid'] = True
            if provider == 'bzzoiro' and response.headers.get('RateLimit'):
                try:
                    entries = parse(response.headers['RateLimit'].encode(), tltype='list')
                    for scope, parameters in entries:
                        if scope == 'football' and isinstance(parameters.get('r'), int) and isinstance(parameters.get('t'), int):
                            remaining, seconds = max(0, parameters['r']), max(0, parameters['t'])
                            if row.get('server_reset_at', 0) > now.timestamp():
                                remaining = min(remaining, row.get('server_remaining', remaining))
                            row.update(server_remaining=remaining, server_reset_at=now.timestamp() + seconds)
                except (ValueError, TypeError):
                    row['server_header_invalid'] = True
            if response.status_code in {401, 402, 403, 429}:
                raw = response.headers.get('Retry-After', '')
                try:
                    delay = float(raw)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(raw) - now).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 3600 if response.status_code != 429 else 60
                until = now.timestamp() + max(1, delay)
                if response.status_code in {402, 403}:
                    endpoint_id = hashlib.sha256(response.request.url.path.encode()).hexdigest()[:16]
                    row.setdefault('blocked_endpoints', {})[endpoint_id] = until
                else:
                    row['blocked_until'] = until
            # Published API-Sports and football-data headers can report usage
            # outside this workflow; exhausted server counters override local ones.
            for header in ('x-ratelimit-requests-remaining', 'x-ratelimit-remaining', 'x-requests-available'):
                if response.headers.get(header) == '0':
                    row['blocked_until'] = max(row.get('blocked_until', 0), now.timestamp() + (86400 if header == 'x-ratelimit-requests-remaining' else 60))

    def snapshot(self):
        now = datetime.now(UTC)
        with self.state() as data:
            return [{'provider': r['provider'], 'account': r.get('account', ''),
                     'day_used': r.get('day_used', 0) if r.get('day') == now.strftime('%Y-%m-%d') else 0,
                     'month_used': r.get('month_used', 0) if r.get('month') == now.strftime('%Y-%m') else 0,
                     'limits': r.get('limits', {}), 'last_status': r.get('last_status'),
                     'last_block': r.get('last_block') if r.get('day') == now.strftime('%Y-%m-%d') else None,
                     'blocked': r.get('blocked_until', 0) > now.timestamp(),
                     'server_remaining': r.get('server_remaining') if r.get('server_reset_at', 0) > now.timestamp() else None} for r in data['accounts'].values()]


class BudgetedAsyncClient(httpx.AsyncClient):
    async def send(self, request, **kwargs):
        identity = request_identity(request) if enabled() else None
        if identity is None:
            return await super().send(request, **kwargs)
        budget = ApiBudget()
        while True:
            reason, delay = budget.reserve(*identity, endpoint=request.url.path)
            if reason == 'allowed':
                break
            if reason == 'pace' and delay <= 60:
                await asyncio.sleep(delay)
                continue
            # A synthetic 429 does not touch the network or consume vendor quota.
            return httpx.Response(429, request=request, headers={'X-Local-API-Budget': reason, 'Retry-After': str(max(60, int(delay)))}, json={'error': 'local_api_budget', 'reason': reason})
        response = await super().send(request, **kwargs)
        budget.observe(identity, response)
        return response
