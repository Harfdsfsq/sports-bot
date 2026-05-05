from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

UTC = timezone.utc
OUT_DIR = Path('.data/exports')
ART_DIR = Path('artifacts/provider-smoke-fast')
JSON_OUT = OUT_DIR / 'latest-provider-smoke-fast.json'
TXT_OUT = OUT_DIR / 'latest-provider-smoke-fast.txt'

SECRET_MARKERS = ('key', 'token', 'secret', 'authorization', 'password', 'apikey', 'api_key', 'appid')


@dataclass(frozen=True)
class Probe:
    name: str
    group: str
    url: str
    method: str = 'GET'
    key_envs: tuple[str, ...] = ()
    headers: dict[str, str] | None = None
    params: dict[str, Any] | None = None
    note: str = ''
    required_secret: bool = True


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _tomorrow() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def _truthy(value: Any) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _first_env(names: tuple[str, ...]) -> tuple[str, str]:
    for name in names:
        value = os.getenv(name)
        if str(value or '').strip():
            return name, str(value).strip()
    return '', ''


def _redact_text(value: Any) -> str:
    text = str(value or '')
    for env_name, env_value in os.environ.items():
        if any(marker in env_name.lower() for marker in SECRET_MARKERS) and env_value and len(env_value) >= 4:
            text = text.replace(env_value, '***')
    text = re.sub(r'([?&](?:apiKey|apikey|api_key|key|token|appid)=)[^&\s]+', r'\1***', text, flags=re.I)
    text = re.sub(r'(Bearer\s+)[A-Za-z0-9._\-]+', r'\1***', text, flags=re.I)
    text = re.sub(r'(Token\s+)[A-Za-z0-9._\-]+', r'\1***', text, flags=re.I)
    return text[:2500]


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return '...'
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:30]:
            if any(marker in str(key).lower() for marker in SECRET_MARKERS):
                out[str(key)] = '***'
            else:
                out[str(key)] = _sanitize(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_sanitize(item, depth + 1) for item in value[:5]]
    if isinstance(value, str):
        return _redact_text(value)[:700]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(value)[:300]


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('results', 'data', 'items', 'events', 'matches', 'response', 'competitions', 'articles', 'news', 'leagues', 'stations'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return [payload] if payload else []


def _shape(payload: Any) -> str:
    if isinstance(payload, list):
        return 'list'
    if isinstance(payload, dict):
        return ','.join(sorted(str(key) for key in payload.keys())[:12])
    return type(payload).__name__


def _status(http_status: int | None, rows_count: int, error: str | None, key_present: bool) -> tuple[str, str]:
    if not key_present:
        return 'MISSING_SECRET', 'required secret not configured'
    if error:
        return 'ERROR', error[:220]
    if http_status in {401, 403}:
        return 'AUTH', f'http {http_status}'
    if http_status == 429:
        return 'RATE_LIMIT', 'http 429'
    if http_status is None:
        return 'ERROR', 'no response'
    if 200 <= http_status < 300:
        if rows_count > 0:
            return 'OK', f'rows={rows_count}'
        return 'EMPTY', 'http ok but no rows detected'
    return 'HTTP_ERROR', f'http {http_status}'


def _apply_key(probe: Probe) -> tuple[Probe, str, bool]:
    key_env, key_value = _first_env(probe.key_envs)
    key_present = bool(key_value) or not probe.required_secret
    headers = dict(probe.headers or {})
    params = dict(probe.params or {})
    url = probe.url
    if key_value:
        for field in ('apiKey', 'apikey', 'api_key', 'key', 'token', 'appid', 'APIkey'):
            if params.get(field) == '${KEY}':
                params[field] = key_value
        for field in list(headers):
            headers[field] = str(headers[field]).replace('${KEY}', key_value)
        url = url.replace('${KEY}', key_value)
    return Probe(probe.name, probe.group, url, probe.method, probe.key_envs, headers, params, probe.note, probe.required_secret), key_env, key_present


async def _run_probe(client: httpx.AsyncClient, sem: asyncio.Semaphore, probe: Probe) -> dict[str, Any]:
    configured, key_env, key_present = _apply_key(probe)
    started = time.perf_counter()
    http_status: int | None = None
    error: str | None = None
    payload: Any = None
    body_preview = ''
    async with sem:
        if key_present:
            try:
                response = await client.request(configured.method, configured.url, headers=configured.headers, params=configured.params)
                http_status = response.status_code
                body_preview = response.text[:1200]
                try:
                    payload = response.json()
                except Exception:
                    payload = None
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
    rows = _rows(payload)
    status, reason = _status(http_status, len(rows), error, key_present)
    return {
        'provider': probe.name,
        'group': probe.group,
        'status': status,
        'reason': reason,
        'http_status': http_status,
        'duration_ms': round((time.perf_counter() - started) * 1000, 1),
        'key_env_used': key_env or None,
        'key_present': key_present,
        'url': _redact_text(configured.url),
        'params': _sanitize(configured.params or {}),
        'payload_shape': _shape(payload),
        'rows_count': len(rows),
        'sample': _sanitize(rows[:3]),
        'body_preview': _redact_text(body_preview),
        'note': probe.note,
    }


def _rapid_probe(name: str, host_env: str, default_host: str, key_envs: tuple[str, ...], path: str = '/sports') -> Probe:
    host = os.getenv(host_env) or default_host
    return Probe(
        name=name,
        group='rapidapi',
        url=f'https://{host}{path}',
        key_envs=key_envs,
        headers={'x-rapidapi-key': '${KEY}', 'x-rapidapi-host': host},
        note=f'RapidAPI host={host}',
    )


def build_probes() -> list[Probe]:
    today = _today()
    tomorrow = _tomorrow()
    return [
        Probe('odds_api_io_account1', 'odds', 'https://api.odds-api.io/v3/events', key_envs=('ODDS_API_IO_KEY',), params={'apiKey': '${KEY}', 'sport': 'football', 'status': 'pending,live', 'limit': 3}, note='events bootstrap'),
        Probe('odds_api_io_account2', 'odds', 'https://api.odds-api.io/v3/events', key_envs=('ODDS_API_IO_KEY_2',), params={'apiKey': '${KEY}', 'sport': 'football', 'status': 'pending,live', 'limit': 3}, note='second odds-api.io account'),
        Probe('bzzoiro_events', 'context', 'https://sports.bzzoiro.com/api/events/', key_envs=('BZZOIRO_API_KEY',), headers={'Authorization': 'Token ${KEY}'}, params={'date_from': today, 'date_to': tomorrow, 'tz': 'UTC', 'page': 1}, note='events endpoint'),
        Probe('bzzoiro_predictions', 'context', 'https://sports.bzzoiro.com/api/predictions/', key_envs=('BZZOIRO_API_KEY',), headers={'Authorization': 'Token ${KEY}'}, params={'upcoming': 'true', 'date_from': today, 'date_to': tomorrow, 'tz': 'UTC', 'page': 1}, note='predictions endpoint'),
        Probe('sstats', 'context', 'https://api.sstats.net/Games/list', key_envs=('SSTATS_API_KEY',), params={'apikey': '${KEY}', 'from': today, 'to': tomorrow, 'limit': 5, 'offset': 0}, note='games list'),
        Probe('football_data', 'context', 'https://api.football-data.org/v4/competitions', key_envs=('FOOTBALL_DATA_API_KEY', 'FOOTBALL_DATA_KEY'), headers={'X-Auth-Token': '${KEY}'}, note='competitions/free access'),
        Probe('thesportsdb', 'context', 'https://www.thesportsdb.com/api/v1/json/${KEY}/search_all_leagues.php', key_envs=('THESPORTSDB_API_KEY',), params={'s': 'Soccer'}, note='uses secret key or 123 if configured'),
        Probe('weatherapi', 'weather', 'https://api.weatherapi.com/v1/current.json', key_envs=('WEATHERAPI_KEY', 'WEATHER_API_KEY', 'WEATHERAPI_API_KEY'), params={'key': '${KEY}', 'q': 'London', 'aqi': 'no'}, note='current weather'),
        Probe('openweathermap', 'weather', 'https://api.openweathermap.org/data/2.5/weather', key_envs=('OPENWEATHERMAP_API_KEY', 'OPENWEATHER_API_KEY', 'OPENWEATHERMAP_KEY'), params={'appid': '${KEY}', 'q': 'London'}, note='current weather'),
        Probe('api_football', 'context', 'https://v3.football.api-sports.io/status', key_envs=('API_FOOTBALL_KEY', 'API_SPORTS_KEY'), headers={'x-apisports-key': '${KEY}'}, note='API-Sports status'),
        Probe('allsportsapi', 'context', 'https://apiv2.allsportsapi.com/football/', key_envs=('ALLSPORTSAPI_API_KEY',), params={'met': 'Leagues', 'APIkey': '${KEY}'}, note='leagues endpoint; free may be limited'),
        Probe('sportlogic', 'odds', 'https://api.sportlogic.io/api/v1/games', key_envs=('SPORTLOGIC_API_KEY', 'SPORTLOGIC_KEY', 'SPORTLOGIC_TOKEN'), headers={'X-API-Key': '${KEY}'}, params={'date_from': today, 'date_to': tomorrow, 'per_page': 5}, note='games endpoint'),
        Probe('newsapi', 'news', 'https://newsapi.org/v2/top-headlines', key_envs=('NEWSAPI_KEY',), params={'apiKey': '${KEY}', 'category': 'sports', 'pageSize': 3, 'language': 'en'}, note='top headlines sports'),
        Probe('currents', 'news', 'https://api.currentsapi.services/v1/latest-news', key_envs=('CURRENTS_API_KEY', 'CURRENTS_KEY'), params={'apiKey': '${KEY}', 'category': 'sports', 'language': 'en'}, note='latest sports news'),
        Probe('gnews', 'news', 'https://gnews.io/api/v4/top-headlines', key_envs=('GNEWS_KEY',), params={'token': '${KEY}', 'topic': 'sports', 'lang': 'en', 'max': 3}, note='top headlines sports'),
        Probe('newsdata', 'news', 'https://newsdata.io/api/1/news', key_envs=('NEWSDATA_API_KEY', 'NEWSDATA_KEY'), params={'apikey': '${KEY}', 'category': 'sports', 'language': 'en'}, note='sports news'),
        Probe('guardian', 'news', 'https://content.guardianapis.com/search', key_envs=('GUARDIAN_API_KEY', 'GUARDIAN_KEY'), params={'api-key': '${KEY}', 'q': 'football', 'page-size': 3}, note='Guardian search'),
        Probe('meteostat_rapidapi', 'weather', 'https://meteostat.p.rapidapi.com/stations/nearby', key_envs=('METEOSTAT_RAPIDAPI_KEY', 'RAPIDAPI_KEY'), headers={'x-rapidapi-key': '${KEY}', 'x-rapidapi-host': os.getenv('METEOSTAT_RAPIDAPI_HOST') or 'meteostat.p.rapidapi.com'}, params={'lat': 51.5072, 'lon': -0.1276, 'limit': 1}, note='RapidAPI Meteostat nearby station'),
        _rapid_probe('oddsfeed_rapidapi', 'ODDS_FEED_RAPIDAPI_HOST', 'odds-feed.p.rapidapi.com', ('ODDS_FEED_RAPIDAPI_KEY', 'RAPIDAPI_KEY')),
        _rapid_probe('sportsbook_rapidapi', 'SPORTSBOOK_RAPIDAPI_HOST', 'sportsbook-api2.p.rapidapi.com', ('SPORTSBOOK_RAPIDAPI_KEY', 'RAPIDAPI_KEY')),
        _rapid_probe('free_football_rapidapi', 'FREE_FOOTBALL_RAPIDAPI_HOST', 'free-api-live-football-data.p.rapidapi.com', ('FREE_FOOTBALL_RAPIDAPI_KEY', 'RAPIDAPI_KEY')),
        _rapid_probe('sportapi7_rapidapi', 'SPORTAPI7_RAPIDAPI_HOST', 'sportapi7.p.rapidapi.com', ('SPORTAPI7_RAPIDAPI_KEY', 'RAPIDAPI_KEY')),
    ]


def _select(probes: list[Probe], raw: str) -> list[Probe]:
    groups = {'all', 'odds', 'context', 'weather', 'news', 'rapidapi', 'core'}
    core = {'odds_api_io_account1', 'odds_api_io_account2', 'bzzoiro_events', 'bzzoiro_predictions', 'sstats', 'football_data', 'thesportsdb', 'weatherapi', 'openweathermap', 'api_football', 'allsportsapi', 'sportlogic'}
    wanted: set[str] = set()
    tokens = [part.strip().lower() for part in str(raw or 'core').split(',') if part.strip()]
    if not tokens:
        tokens = ['core']
    for token in tokens:
        if token == 'all':
            return probes
        if token == 'core':
            wanted.update(core)
        elif token in groups:
            wanted.update(p.name for p in probes if p.group == token)
        else:
            wanted.add(token)
    return [p for p in probes if p.name.lower() in wanted or p.name.lower().replace('_account1', '') in wanted]


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in results:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {
        'total': len(results),
        'ok': counts.get('OK', 0),
        'missing_secret': counts.get('MISSING_SECRET', 0),
        'warnings': counts.get('EMPTY', 0) + counts.get('RATE_LIMIT', 0),
        'errors': counts.get('ERROR', 0) + counts.get('AUTH', 0) + counts.get('HTTP_ERROR', 0),
        'by_status': counts,
    }


def _write(payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    text = _render_text(payload)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    TXT_OUT.write_text(text + '\n', encoding='utf-8')
    (ART_DIR / 'provider-smoke-fast.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (ART_DIR / 'provider-smoke-fast.txt').write_text(text + '\n', encoding='utf-8')


def _render_text(payload: dict[str, Any]) -> str:
    lines = [
        '🧪 Provider Smoke FAST',
        f"• UTC: {payload.get('created_at_utc')}",
        f"• providers: {payload.get('providers_arg')}",
        f"• duration: {payload.get('duration_seconds')}s / limit {payload.get('max_seconds')}s",
        f"• summary: OK {payload['summary']['ok']} | missing {payload['summary']['missing_secret']} | warn {payload['summary']['warnings']} | errors {payload['summary']['errors']}",
        '',
        '📡 Results',
    ]
    for row in payload.get('results', []):
        lines.append(f"• {row['provider']}: {row['status']} | http={row.get('http_status')} | rows={row.get('rows_count')} | {row.get('reason')}")
        if row.get('status') != 'OK':
            lines.append(f"  url: {row.get('url')}")
            if row.get('body_preview'):
                lines.append(f"  body: {str(row.get('body_preview'))[:500]}")
        elif _truthy(os.getenv('PROVIDER_SMOKE_SHOW_OK_SAMPLES')) and row.get('sample'):
            lines.append(f"  sample: {json.dumps(row.get('sample'), ensure_ascii=False)[:500]}")
    lines += ['', '📁 Attach these files to ChatGPT:', str(JSON_OUT), str(TXT_OUT)]
    return '\n'.join(lines)


async def main_async(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    probes = _select(build_probes(), args.providers)
    timeout = httpx.Timeout(float(args.timeout), connect=min(4.0, float(args.timeout)))
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = [_run_probe(client, sem, probe) for probe in probes]
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=float(args.max_seconds))
        except asyncio.TimeoutError:
            results = []
            for task in tasks:
                if hasattr(task, 'done') and task.done():
                    try:
                        results.append(task.result())
                    except Exception:
                        pass
    duration = round(time.perf_counter() - started, 2)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'mode': 'provider_smoke_fast',
        'providers_arg': args.providers,
        'max_seconds': args.max_seconds,
        'timeout_seconds': args.timeout,
        'duration_seconds': duration,
        'summary': _summary(results),
        'results': results,
    }
    _write(payload)
    print(_render_text(payload))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Fast direct API provider smoke')
    parser.add_argument('--providers', default=os.getenv('PROVIDER_SMOKE_FAST_PROVIDERS', 'core'))
    parser.add_argument('--timeout', type=float, default=float(os.getenv('PROVIDER_SMOKE_FAST_TIMEOUT', '7')))
    parser.add_argument('--max-seconds', type=float, default=float(os.getenv('PROVIDER_SMOKE_FAST_MAX_SECONDS', '105')))
    parser.add_argument('--concurrency', type=int, default=int(os.getenv('PROVIDER_SMOKE_FAST_CONCURRENCY', '6')))
    return parser.parse_args()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
