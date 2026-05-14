from __future__ import annotations

"""Low-quota API repair probe for provider-smoke.

This probe is intentionally cheap:
- odds-api.io: 1 events request + at most 1 odds/multi request per account;
- bzzoiro: 1 events request + optional odds comparison for one matched event;
- sstats: 1 day-list request + optional one detail/stat request;
- sportlogic: up to 3 fixture endpoint variants + optional one odds request.

It writes JSON/TXT artifacts under .data/exports and never blocks the workflow.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path('.data/exports')
JSON_PATH = OUT_DIR / 'latest-provider-api-min-repair-probe.json'
TXT_PATH = OUT_DIR / 'latest-provider-api-min-repair-probe.txt'


def _secret(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or '').strip()
        if value:
            return value
    return ''


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return f'list[{len(value)}]'
    if isinstance(value, dict):
        return ','.join(sorted(str(k) for k in value.keys())[:10])
    return type(value).__name__


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('results', 'data', 'events', 'matches', 'fixtures', 'response', 'items', 'games', 'odds'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _dig(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ('name', 'short_name', 'shortName', 'display_name', 'displayName', 'title', 'label'):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ''
    return str(value or '').strip()


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _dig(row, *key.split('.')) if '.' in key else row.get(key)
        text = _text(value)
        if text:
            return text
    return ''


def _parse_dt(value: Any) -> str | None:
    if not value:
        return None
    try:
        from app.utils import parse_datetime
        return parse_datetime(str(value)).astimezone(UTC).isoformat()
    except Exception:
        return None


def _event(provider: str, row: dict[str, Any]) -> dict[str, Any] | None:
    if provider == 'odds_api_io':
        home = _first(row, ('home', 'home_team', 'homeTeam.name'))
        away = _first(row, ('away', 'away_team', 'awayTeam.name'))
        league = _first(row, ('league.name', 'league', 'competition.name'))
        start = _parse_dt(row.get('date') or row.get('commence_time') or row.get('start'))
        source_id = str(row.get('id') or '')
    elif provider == 'bzzoiro':
        event = row.get('event') if isinstance(row.get('event'), dict) else row
        home = _first(event, ('home_team', 'home_team.name', 'home_team_obj.name', 'home.name', 'home'))
        away = _first(event, ('away_team', 'away_team.name', 'away_team_obj.name', 'away.name', 'away'))
        league = _first(event, ('league.name', 'league', 'competition.name'))
        start = _parse_dt(event.get('event_date') or event.get('start_time') or event.get('date') or event.get('start'))
        source_id = str(event.get('id') or row.get('id') or '')
    elif provider == 'sstats':
        home = _first(row, ('homeTeamName', 'HomeTeamName', 'home_team', 'home.name', 'home'))
        away = _first(row, ('awayTeamName', 'AwayTeamName', 'away_team', 'away.name', 'away'))
        league = _first(row, ('leagueName', 'LeagueName', 'league.name', 'league'))
        start = _parse_dt(row.get('date') or row.get('Date') or row.get('start'))
        source_id = str(row.get('id') or row.get('Id') or row.get('gameId') or '')
    else:
        home = _first(row, ('home_team', 'homeTeam', 'home.name', 'home_team.name', 'localteam.name', 'home'))
        away = _first(row, ('away_team', 'awayTeam', 'away.name', 'away_team.name', 'visitorteam.name', 'away'))
        league = _first(row, ('league.name', 'competition.name', 'tournament.name', 'league', 'competition'))
        start = _parse_dt(row.get('date') or row.get('start_time') or row.get('starts_at') or row.get('kickoff') or row.get('commence_time'))
        source_id = str(row.get('id') or row.get('game_id') or row.get('fixture_id') or row.get('match_id') or '')
    if not home or not away:
        return None
    return {'provider': provider, 'id': source_id, 'home': home, 'away': away, 'league': league, 'start': start}


async def _get(client: httpx.AsyncClient, provider: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[Any, dict[str, Any]]:
    started = datetime.now(UTC)
    safe_params = {k: ('***' if 'key' in k.lower() or 'token' in k.lower() else v) for k, v in (params or {}).items()}
    try:
        r = await client.get(url, params=params, headers=headers)
        try:
            payload = r.json()
        except Exception:
            payload = None
        return payload, {
            'provider': provider,
            'url_path': url.split('://', 1)[-1].split('/', 1)[-1],
            'status': r.status_code,
            'ok': r.status_code == 200,
            'params': safe_params,
            'shape': _shape(payload),
            'rows': len(_rows(payload)),
            'body_preview': r.text[:500],
            'duration_ms': round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
        }
    except Exception as exc:
        return None, {'provider': provider, 'ok': False, 'status': 'request_error', 'error': f'{type(exc).__name__}: {exc}', 'params': safe_params}


async def _probe_odds(client: httpx.AsyncClient) -> dict[str, Any]:
    key1 = _secret('ODDS_API_IO_KEY')
    key2 = _secret('ODDS_API_IO_KEY_2', 'ODDS_API_IO_KEY2')
    if not key1:
        return {'provider': 'odds_api_io', 'status': 'missing_key'}
    now = datetime.now(UTC).replace(microsecond=0)
    params = {'apiKey': key1, 'sport': 'football', 'status': 'pending,live', 'from': now.isoformat().replace('+00:00', 'Z'), 'to': (now + timedelta(days=1)).isoformat().replace('+00:00', 'Z'), 'limit': 60, 'page': 1}
    payload, attempt = await _get(client, 'odds_api_io', 'https://api.odds-api.io/v3/events', params=params)
    events = [_event('odds_api_io', row) for row in _rows(payload)]
    events = [x for x in events if x]
    event_ids = [int(x['id']) for x in events[:8] if str(x.get('id') or '').isdigit()]
    odds_attempts: list[dict[str, Any]] = []
    for name, key, books in [('account1', key1, 'Bet365,Unibet'), ('account2', key2, 'Betfair Exchange,Sbobet')]:
        if not key or not event_ids:
            odds_attempts.append({'account': name, 'status': 'missing_key_or_events', 'bookmakers': books})
            continue
        odds_payload, odds_attempt = await _get(client, 'odds_api_io', 'https://api.odds-api.io/v3/odds/multi', params={'apiKey': key, 'eventIds': ','.join(map(str, event_ids)), 'bookmakers': books})
        odds_attempt['account'] = name
        odds_attempt['bookmakers'] = books
        odds_attempt['odds_events'] = len(_rows(odds_payload))
        odds_attempts.append(odds_attempt)
    return {'provider': 'odds_api_io', 'status': 'ok' if attempt.get('ok') else 'request_failed', 'requests_used': 1 + sum(1 for a in odds_attempts if a.get('ok') or isinstance(a.get('status'), int)), 'events_raw': len(_rows(payload)), 'events_parsed': len(events), 'samples': events[:5], 'attempt': attempt, 'odds_attempts': odds_attempts}


async def _probe_bzzoiro(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret('BZZOIRO_API_KEY')
    if not key:
        return {'provider': 'bzzoiro', 'status': 'missing_key'}
    today = datetime.now(UTC).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    headers = {'Authorization': f'Token {key}'}
    payload, attempt = await _get(client, 'bzzoiro', 'https://sports.bzzoiro.com/api/v2/events/', params={'date_from': today, 'date_to': tomorrow, 'status': 'notstarted', 'limit': 80, 'offset': 0}, headers=headers)
    if not attempt.get('ok'):
        payload, attempt = await _get(client, 'bzzoiro', 'https://sports.bzzoiro.com/api/events/', params={'date_from': today, 'date_to': tomorrow, 'status': 'notstarted', 'limit': 80, 'offset': 0}, headers=headers)
    events = [_event('bzzoiro', row) for row in _rows(payload)]
    events = [x for x in events if x]
    return {'provider': 'bzzoiro', 'status': 'ok' if attempt.get('ok') else 'request_failed', 'requests_used': 1, 'events_raw': len(_rows(payload)), 'events_parsed': len(events), 'samples': events[:5], 'attempt': attempt}


async def _probe_sstats(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret('SSTATS_API_KEY')
    today = datetime.now(UTC).date().isoformat()
    headers = {}
    params: dict[str, Any] = {'date': today, 'limit': 1000, 'offset': 0, 'timeZone': 0}
    if key:
        params['apikey'] = key
    payload, attempt = await _get(client, 'sstats', 'https://api.sstats.net/Games/list', params=params, headers=headers)
    if not attempt.get('ok'):
        payload, attempt = await _get(client, 'sstats', 'https://api.sstats.net/Games/list', params={'today': 'true', 'limit': 1000, 'offset': 0, **({'apikey': key} if key else {})})
    rows = _rows(payload)
    events = [_event('sstats', row) for row in rows]
    events = [x for x in events if x]
    return {'provider': 'sstats', 'status': 'ok' if attempt.get('ok') else 'request_failed', 'key_present': bool(key), 'requests_used': 1, 'events_raw': len(rows), 'events_parsed': len(events), 'samples': events[:5], 'attempt': attempt}


async def _probe_sportlogic(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret('SPORTLOGIC_API_KEY', 'SPORTLOGIC_KEY', 'SPORTLOGIC_TOKEN')
    if not key:
        return {'provider': 'sportlogic', 'status': 'missing_key'}
    base = str(os.getenv('SPORTLOGIC_BASE_URL') or 'https://api.sportlogic.io/api/v1').rstrip('/')
    header_name = str(os.getenv('SPORTLOGIC_HEADER_NAME') or 'X-API-Key').strip() or 'X-API-Key'
    headers = {'Accept': 'application/json', header_name: key}
    today = datetime.now(UTC).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    attempts: list[dict[str, Any]] = []
    payload = None
    for params in ({'date_from': today, 'date_to': tomorrow, 'per_page': 80}, {'from': today, 'to': tomorrow, 'per_page': 80}, {'date': today, 'per_page': 80}):
        payload, attempt = await _get(client, 'sportlogic', f'{base}/games', params=params, headers=headers)
        attempts.append(attempt)
        if attempt.get('ok') and _rows(payload):
            break
    rows = _rows(payload)
    events = [_event('sportlogic', row) for row in rows]
    events = [x for x in events if x]
    odds_attempt = None
    first_id = next((x.get('id') for x in events if x.get('id')), '')
    if first_id:
        odds_payload, odds_attempt = await _get(client, 'sportlogic', f'{base}/games/{first_id}/odds', headers=headers)
        if odds_attempt and not odds_attempt.get('ok'):
            odds_payload, odds_attempt = await _get(client, 'sportlogic', f'{base}/odds', params={'game_id': first_id}, headers=headers)
        if odds_attempt:
            odds_attempt['odds_rows'] = len(_rows(odds_payload))
    return {'provider': 'sportlogic', 'status': 'ok' if any(a.get('ok') for a in attempts) else 'request_failed', 'requests_used': len(attempts) + (1 if odds_attempt else 0), 'events_raw': len(rows), 'events_parsed': len(events), 'samples': events[:5], 'attempts': attempts, 'odds_attempt': odds_attempt}


def _match_summary(payload: dict[str, Any]) -> dict[str, Any]:
    odds_events = (payload.get('odds_api_io') or {}).get('samples') or []
    out: dict[str, Any] = {}
    try:
        from app.utils import score_event_match
        for provider in ('bzzoiro', 'sstats', 'sportlogic'):
            events = (payload.get(provider) or {}).get('samples') or []
            matched = 0
            best_samples = []
            for event in events:
                best = 0.0
                best_odds = None
                for odds in odds_events:
                    if not event.get('start') or not odds.get('start'):
                        continue
                    score, quality = score_event_match('soccer', odds['home'], odds['away'], odds['start'], odds.get('league') or '', event['home'], event['away'], event['start'], event.get('league') or '', exact_tolerance_hours=12, fuzzy_tolerance_hours=18)
                    if score > best:
                        best = float(score)
                        best_odds = {'home': odds['home'], 'away': odds['away'], 'score': round(best, 1), 'quality': quality}
                if best >= 54:
                    matched += 1
                if len(best_samples) < 3:
                    best_samples.append({'event': event, 'best_odds': best_odds})
            out[provider] = {'sample_events': len(events), 'matched_to_odds_sample': matched, 'best_samples': best_samples}
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'
    return out


def _render(payload: dict[str, Any]) -> str:
    lines = ['🧪 Minimal API repair probe', f"• created_at_utc: {payload.get('created_at_utc')}", '• quota policy: minimal important requests only', '', '| provider | status | req | raw | parsed | note |', '| --- | --- | ---: | ---: | ---: | --- |']
    for provider in ('odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'):
        item = payload.get(provider) or {}
        note = ''
        if item.get('status') == 'missing_key':
            note = 'нет ключа'
        elif int(item.get('events_raw') or 0) <= 0:
            note = 'запрос/окно/endpoint не дал строк'
        elif int(item.get('events_parsed') or 0) <= 0:
            note = 'parser не достал команды/время'
        else:
            note = 'есть parse-события'
        lines.append(f"| {provider} | {item.get('status')} | {item.get('requests_used', 0)} | {item.get('events_raw', 0)} | {item.get('events_parsed', 0)} | {note} |")
    lines.append('')
    lines.append('🔗 Sample overlap vs odds-api.io')
    for provider, row in (payload.get('sample_overlap') or {}).items():
        if isinstance(row, dict):
            lines.append(f"• {provider}: {row.get('matched_to_odds_sample')}/{row.get('sample_events')} sample events matched")
    lines.append('')
    lines.append('Дальше смотри JSON artifact: attempts.body_preview, params, shapes, odds_attempts.')
    return '\n'.join(lines) + '\n'


async def main_async() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(float(os.getenv('PROVIDER_API_MIN_PROBE_TIMEOUT', '16')), connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        results = await asyncio.gather(_probe_odds(client), _probe_bzzoiro(client), _probe_sstats(client), _probe_sportlogic(client), return_exceptions=True)
    payload: dict[str, Any] = {'created_at_utc': datetime.now(UTC).isoformat()}
    for name, result in zip(('odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'), results):
        payload[name] = {'provider': name, 'status': 'failed', 'error': f'{type(result).__name__}: {result}'} if isinstance(result, Exception) else result
    payload['sample_overlap'] = _match_summary(payload)
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    TXT_PATH.write_text(_render(payload), encoding='utf-8')
    print(TXT_PATH.read_text(encoding='utf-8'))
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        TXT_PATH.write_text(f'provider_api_min_repair_probe failed: {type(exc).__name__}: {exc}\n', encoding='utf-8')
        print(TXT_PATH.read_text(encoding='utf-8'))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
