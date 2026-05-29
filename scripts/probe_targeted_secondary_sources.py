from __future__ import annotations

"""Tiny per-run probes for secondary odds/context providers.

The goal is discovery, not production publication: check whether Highlightly,
AllSportsAPI and API-Football can cover the current value/near-miss shortlist.
Every provider has a small per-run request cap and stops on 429/5xx.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-targeted-secondary-provider-probe.json'
CACHE = ROOT / '.data' / 'provider_cache' / 'targeted-secondary-probes'


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def rows_from(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('items', 'rows', 'candidates', 'selected_all', 'evaluated', 'fallback_evaluated', 'latest_rescue_candidates'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
        elif isinstance(value, dict):
            out.append(value)
    selected = payload.get('selected')
    if isinstance(selected, dict):
        out.append(selected)
    samples = payload.get('samples') if isinstance(payload.get('samples'), dict) else {}
    for key in ('fallback_evaluated', 'rescue_candidates'):
        value = samples.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
    return out


def metric(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    for key in keys:
        if row.get(key) not in (None, ''):
            return row.get(key)
        if metrics.get(key) not in (None, ''):
            return metrics.get(key)
    return None


def target_dates(targets: list[dict[str, Any]], fallback_days: int = 1) -> list[str]:
    dates: list[str] = []
    for row in targets:
        raw = str(metric(row, 'commence_time', 'kickoff_utc', 'start_time', 'kickoff') or '').strip()
        if raw[:10] and raw[:4].isdigit() and raw[:10] not in dates:
            dates.append(raw[:10])
    if not dates:
        today = datetime.now(UTC).date()
        dates = [(today + timedelta(days=i)).isoformat() for i in range(max(1, fallback_days))]
    return dates[:3]


def response_rows(payload: Any) -> list[dict[str, Any]]:
    # Provider wrappers differ: Highlightly returns {data:[...]}, API-Football
    # returns {response:[...]}, AllSportsAPI often returns {result:[...]}.
    rows = extract_rows(payload)
    if len(rows) == 1 and isinstance(rows[0], dict):
        inner = rows[0]
        for key in ('data', 'response', 'result', 'items', 'events', 'matches'):
            if isinstance(inner.get(key), list):
                return [x for x in inner[key] if isinstance(x, dict)]
    return rows


def target_rows(limit: int = 8) -> list[dict[str, Any]]:
    paths = [
        EXPORT / 'latest-rejected-near-miss-report.json',
        EXPORT / 'latest-controlled-fallback-report.json',
        EXPORT / 'latest-rescue-candidates.json',
        EXPORT / 'latest-near-miss-enrichment-queue.json',
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(rows_from(load_json(path, {})))
    # fallback to soon upcoming inventory if no candidate rows exist
    if not rows:
        day = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or datetime.now(UTC).date().isoformat())
        inv = load_json(Path('.data/day_inventory') / f'{day}.json', {})
        rows.extend(rows_from(inv))
    seen: set[str] = set(); out: list[dict[str, Any]] = []
    for row in rows:
        home = str(metric(row, 'home_team', 'home') or '').strip()
        away = str(metric(row, 'away_team', 'away') or '').strip()
        if not home or not away:
            continue
        key = f"{home.lower()}|{away.lower()}|{metric(row, 'commence_time', 'kickoff_utc', 'start_time') or ''}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


class ProbeBudget:
    def __init__(self, provider: str, limit: int) -> None:
        self.provider = provider
        self.limit = max(0, limit)
        self.used = 0
        self.stopped = False
        self.http_statuses: list[int] = []
        self.errors: list[str] = []

    def can(self) -> bool:
        return not self.stopped and (self.limit <= 0 or self.used < self.limit)

    async def get(self, client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
        if not self.can():
            return None
        self.used += 1
        try:
            resp = await client.get(url, headers=headers or {}, params=params or {})
            self.http_statuses.append(resp.status_code)
            if resp.status_code == 429 or resp.status_code >= 500:
                self.stopped = True
            if resp.status_code >= 400:
                self.errors.append(f'{resp.status_code}:{resp.text[:200]}')
                return None
            try:
                return resp.json()
            except Exception:
                return {'_text_preview': resp.text[:500]}
        except Exception as exc:
            self.errors.append(f'{type(exc).__name__}: {exc}')
            self.stopped = True
            return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('response', 'data', 'results', 'items', 'events', 'matches'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload]
    return []


async def probe_highlightly(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('HIGHLIGHTLY_API_KEY') or os.getenv('HIGHLIGHTLY_KEY') or os.getenv('HIGHLIGHTLY_RAPIDAPI_KEY')
    budget = ProbeBudget('highlightly', as_int(os.getenv('HIGHLIGHTLY_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'highlightly', 'configured': bool(key), 'requests': 0, 'matches_seen': 0, 'matched_targets': 0, 'odds_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': [], 'auth_header_mode': 'x-rapidapi-key'}
    if not key:
        return result
    # Docs: direct base is https://soccer.highlightly.net and the API expects
    # x-rapidapi-key even for Highlightly keys. RapidAPI also requires host.
    base = (os.getenv('HIGHLIGHTLY_BASE_URL') or 'https://soccer.highlightly.net').rstrip('/')
    host = os.getenv('HIGHLIGHTLY_RAPIDAPI_HOST') or ('football-highlights-api.p.rapidapi.com' if 'rapidapi' in base else '')
    headers = {'x-rapidapi-key': key, 'accept': 'application/json', 'user-agent': 'HARIZON sports-bot secondary-provider-probe'}
    if host or os.getenv('HIGHLIGHTLY_SEND_RAPIDAPI_HOST', '').lower() in {'1', 'true', 'yes', 'on', 'force'}:
        headers['x-rapidapi-host'] = host or 'football-highlights-api.p.rapidapi.com'
    dates = target_dates(targets, fallback_days=2)[:2]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, f'{base}/matches', headers=headers, params={'date': d, 'timezone': 'Etc/UTC', 'limit': 100})
        rows.extend(response_rows(payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors, 'matches_seen': len(rows), 'samples': rows[:3]})
    result['matched_targets'] = fuzzy_count(rows, targets)
    if rows and budget.can():
        # Cheap odds probe by date; if the free plan exposes odds, this tells us
        # whether Highlightly can become a second odds-source later.
        payload = await budget.get(client, f'{base}/odds', headers=headers, params={'date': dates[0], 'oddsType': 'prematch', 'timezone': 'Etc/UTC', 'limit': 5})
        result['odds_probe_rows'] = len(response_rows(payload))
        result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result

async def probe_allsportsapi(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('ALLSPORTSAPI_API_KEY')
    budget = ProbeBudget('allsportsapi', as_int(os.getenv('ALLSPORTSAPI_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'allsportsapi', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'odds_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('ALLSPORTSAPI_BASE_URL') or 'https://apiv2.allsportsapi.com/football/').rstrip('/') + '/'
    dates = target_dates(targets, fallback_days=2)
    payload = await budget.get(client, base, params={'met': 'Fixtures', 'APIkey': key, 'from': min(dates), 'to': max(dates), 'timezone': 'UTC'})
    rows = response_rows(payload.get('result') if isinstance(payload, dict) else payload)
    result['fixtures_seen'] = len(rows); result['matched_targets'] = fuzzy_count(rows, targets); result['samples'] = rows[:3]
    # one cheap odds probe for the first matched/available event
    if rows and budget.can():
        event_id = str(rows[0].get('event_key') or rows[0].get('match_id') or '')
        if event_id:
            odds_payload = await budget.get(client, base, params={'met': 'Odds', 'APIkey': key, 'matchId': event_id})
            result['odds_probe_rows'] = len(response_rows(odds_payload.get('result') if isinstance(odds_payload, dict) else odds_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


async def probe_api_football(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('API_FOOTBALL_KEY') or os.getenv('API_FOOTBALL_API_KEY') or os.getenv('RAPIDAPI_KEY')
    budget = ProbeBudget('api_football', as_int(os.getenv('API_FOOTBALL_PROBE_MAX_REQUESTS'), 4))
    result = {'provider': 'api_football', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'statistics_probe_rows': 0, 'lineups_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('API_FOOTBALL_BASE_URL') or 'https://v3.football.api-sports.io').rstrip('/')
    headers = {'x-apisports-key': key}
    if os.getenv('API_FOOTBALL_RAPIDAPI_HOST'):
        headers = {'x-rapidapi-key': key, 'x-rapidapi-host': os.getenv('API_FOOTBALL_RAPIDAPI_HOST') or 'api-football-v1.p.rapidapi.com'}
    rows: list[dict[str, Any]] = []
    for date in target_dates(targets, fallback_days=2)[:2]:
        if not budget.can():
            break
        payload = await budget.get(client, f'{base}/fixtures', headers=headers, params={'date': date, 'timezone': 'UTC'})
        rows.extend(response_rows(payload))
    result['fixtures_seen'] = len(rows); result['matched_targets'] = fuzzy_count(rows, targets); result['samples'] = rows[:3]
    fixture_id = ''
    if rows:
        fixture = rows[0].get('fixture') if isinstance(rows[0].get('fixture'), dict) else {}
        fixture_id = str(fixture.get('id') or rows[0].get('fixture_id') or '')
    if fixture_id and budget.can():
        stats_payload = await budget.get(client, f'{base}/fixtures/statistics', headers=headers, params={'fixture': fixture_id})
        result['statistics_probe_rows'] = len(response_rows(stats_payload))
    if fixture_id and budget.can():
        lineup_payload = await budget.get(client, f'{base}/fixtures/lineups', headers=headers, params={'fixture': fixture_id})
        result['lineups_probe_rows'] = len(response_rows(lineup_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


def name_parts(row: dict[str, Any]) -> tuple[str, str]:
    home = str(row.get('home_team') or row.get('home') or row.get('event_home_team') or '').lower()
    away = str(row.get('away_team') or row.get('away') or row.get('event_away_team') or '').lower()
    if not home and isinstance(row.get('homeTeam'), dict):
        home = str(row['homeTeam'].get('name') or row['homeTeam'].get('displayName') or '').lower()
    if not away and isinstance(row.get('awayTeam'), dict):
        away = str(row['awayTeam'].get('name') or row['awayTeam'].get('displayName') or '').lower()
    if not home and isinstance(row.get('teams'), dict):
        home_obj = row['teams'].get('home')
        away_obj = row['teams'].get('away')
        home = str(home_obj.get('name') if isinstance(home_obj, dict) else home_obj or '').lower()
        away = str(away_obj.get('name') if isinstance(away_obj, dict) else away_obj or '').lower()
    return home, away


def fuzzy_count(rows: list[dict[str, Any]], targets: list[dict[str, Any]]) -> int:
    count = 0
    for t in targets:
        th, ta = name_parts({'home_team': metric(t, 'home_team', 'home'), 'away_team': metric(t, 'away_team', 'away')})
        if not th or not ta:
            continue
        th_tokens = {x for x in th.replace('-', ' ').split() if len(x) >= 3}
        ta_tokens = {x for x in ta.replace('-', ' ').split() if len(x) >= 3}
        for row in rows:
            rh, ra = name_parts(row)
            if th_tokens and ta_tokens and th_tokens.intersection(rh.split()) and ta_tokens.intersection(ra.split()):
                count += 1
                break
    return count


async def main_async() -> int:
    if os.getenv('EXTERNAL_SIGNAL_PROBES_ENABLED', 'true').lower() not in {'1', 'true', 'yes', 'on', 'force'}:
        write_json(OUT, {'status': 'disabled'})
        return 0
    targets = target_rows(as_int(os.getenv('SECONDARY_PROVIDER_PROBE_TARGET_LIMIT'), 8))
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        results = await asyncio.gather(
            probe_highlightly(client, targets),
            probe_allsportsapi(client, targets),
            probe_api_football(client, targets),
        )
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_count': len(targets),
        'target_sample': [
            {'home': metric(t, 'home_team', 'home'), 'away': metric(t, 'away_team', 'away'), 'kickoff': metric(t, 'commence_time', 'kickoff_utc', 'start_time')}
            for t in targets[:8]
        ],
        'providers': {row['provider']: row for row in results},
        'notes': [
            'Probe-only: no publication decisions are changed.',
            'All limits are per-run; any 429/5xx stops that provider for this probe only.',
            'If matched_targets and odds/stat rows are non-zero over several runs, promote the provider into runtime enrichment.',
        ],
    }
    write_json(OUT, payload)
    CACHE.mkdir(parents=True, exist_ok=True)
    write_json(CACHE / f"probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main_async()))
