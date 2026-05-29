from __future__ import annotations

"""Tiny per-run probes for secondary odds/context providers.

Probe-only: no publication decisions are changed directly here.  The companion
``merge_targeted_secondary_context.py`` can turn successful fixture matches into
context-source evidence before controlled fallback evaluates candidates.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-targeted-secondary-provider-probe.json'
CACHE = ROOT / '.data' / 'provider_cache' / 'targeted-secondary-probes'
RUN_CACHE = CACHE / 'current-run.json'
NEAR_MISS_LEDGER = ROOT / '.data' / 'rejected-near-miss-ledger.jsonl'


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
    for key in ('items', 'rows', 'candidates', 'selected_all', 'evaluated', 'fallback_evaluated', 'latest_rescue_candidates', 'watchlist'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
        elif isinstance(value, dict):
            out.append(value)
    selected = payload.get('selected')
    if isinstance(selected, dict):
        out.append(selected)
    samples = payload.get('samples') if isinstance(payload.get('samples'), dict) else {}
    for key in ('fallback_evaluated', 'rescue_candidates', 'near_misses'):
        value = samples.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
    # nested near-miss reports often expose sample rows only
    sample = payload.get('sample')
    if isinstance(sample, list):
        out.extend([x for x in sample if isinstance(x, dict)])
    return out


def ledger_rows(limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if NEAR_MISS_LEDGER.exists():
            for line in NEAR_MISS_LEDGER.read_text(encoding='utf-8').splitlines()[-max(limit * 3, 20):]:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return rows[-limit:]
    return rows[-limit:]


def metric(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    raw_metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    for key in keys:
        if row.get(key) not in (None, ''):
            return row.get(key)
        if metrics.get(key) not in (None, ''):
            return metrics.get(key)
        if raw.get(key) not in (None, ''):
            return raw.get(key)
        if raw_metrics.get(key) not in (None, ''):
            return raw_metrics.get(key)
    return None


def target_rows(limit: int = 8) -> list[dict[str, Any]]:
    paths = [
        EXPORT / 'latest-rejected-near-miss-report.json',
        EXPORT / 'latest-controlled-fallback-report.json',
        EXPORT / 'latest-rescue-candidates.json',
        EXPORT / 'latest-near-miss-enrichment-queue.json',
        ROOT / '.logs' / 'debug-last-run.json',
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(rows_from(load_json(path, {})))
    rows.extend(ledger_rows(limit=limit * 2))
    # fallback to soon upcoming inventory if no candidate rows exist
    if not rows:
        day = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or datetime.now(UTC).date().isoformat())
        inv = load_json(Path('.data/day_inventory') / f'{day}.json', {})
        rows.extend(rows_from(inv))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
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
                self.errors.append(f'{resp.status_code}:{resp.text[:220]}')
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
        for key in ('response', 'data', 'results', 'items', 'events', 'matches', 'result'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload]
    return []


def name_parts(row: dict[str, Any]) -> tuple[str, str]:
    home = str(row.get('home_team') or row.get('home') or row.get('event_home_team') or '').lower()
    away = str(row.get('away_team') or row.get('away') or row.get('event_away_team') or '').lower()
    if not home and isinstance(row.get('homeTeam'), dict):
        home = str(row['homeTeam'].get('name') or '').lower()
        away = str(row.get('awayTeam', {}).get('name') if isinstance(row.get('awayTeam'), dict) else '').lower()
    if not home and isinstance(row.get('teams'), dict):
        home_obj = row['teams'].get('home')
        away_obj = row['teams'].get('away')
        home = str(home_obj.get('name') if isinstance(home_obj, dict) else '').lower()
        away = str(away_obj.get('name') if isinstance(away_obj, dict) else '').lower()
    return home, away


def tokens(text: str) -> set[str]:
    normalized = text.lower().replace('-', ' ').replace('.', ' ')
    return {x for x in normalized.split() if len(x) >= 3 and x not in {'club', 'football', 'futbol', 'fc', 'sc', 'cf'}}


def kickoff_date(row: dict[str, Any]) -> str:
    value = row.get('date') or row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time') or row.get('event_date')
    if isinstance(value, dict):
        value = value.get('date') or value.get('datetime')
    text = str(value or '')
    return text[:10] if len(text) >= 10 else ''


def target_match_key(row: dict[str, Any]) -> str:
    value = str(metric(row, 'match_key', 'canonical_match_id') or '').strip()
    if value:
        return value
    home = str(metric(row, 'home_team', 'home') or '').strip().lower()
    away = str(metric(row, 'away_team', 'away') or '').strip().lower()
    date = str(metric(row, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]
    return f'soccer|{home}|{away}|{date}'


def matched_contexts(rows: list[dict[str, Any]], targets: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in targets:
        th, ta = name_parts({'home_team': metric(t, 'home_team', 'home'), 'away_team': metric(t, 'away_team', 'away')})
        if not th or not ta:
            continue
        th_tokens = tokens(th)
        ta_tokens = tokens(ta)
        t_date = str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]
        for row in rows:
            rh, ra = name_parts(row)
            if not rh or not ra:
                continue
            same_date = not t_date or not kickoff_date(row) or kickoff_date(row) == t_date
            direct = th_tokens.intersection(tokens(rh)) and ta_tokens.intersection(tokens(ra))
            swapped = th_tokens.intersection(tokens(ra)) and ta_tokens.intersection(tokens(rh))
            if same_date and (direct or swapped):
                mk = target_match_key(t)
                if mk not in seen:
                    seen.add(mk)
                    out.append({
                        'match_key': mk,
                        'home_team': metric(t, 'home_team', 'home'),
                        'away_team': metric(t, 'away_team', 'away'),
                        'kickoff_utc': metric(t, 'commence_time', 'kickoff_utc', 'start_time'),
                        'provider': provider,
                        'provider_event_id': row.get('id') or row.get('event_key') or row.get('fixture', {}).get('id') if isinstance(row.get('fixture'), dict) else row.get('id'),
                        'provider_league': row.get('league'),
                        'provider_country': row.get('country'),
                        'provider_date': row.get('date') or row.get('event_date'),
                    })
                break
    return out


async def probe_highlightly(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('HIGHLIGHTLY_API_KEY') or os.getenv('HIGHLIGHTLY_KEY') or os.getenv('RAPIDAPI_KEY')
    budget = ProbeBudget('highlightly', as_int(os.getenv('HIGHLIGHTLY_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'highlightly', 'configured': bool(key), 'requests': 0, 'matches_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('HIGHLIGHTLY_BASE_URL') or 'https://soccer.highlightly.net').rstrip('/')
    host = os.getenv('HIGHLIGHTLY_RAPIDAPI_HOST') or ('football-highlights-api.p.rapidapi.com' if 'rapidapi' in base else '')
    if host:
        headers = {'x-rapidapi-key': key, 'x-rapidapi-host': host}
        result['auth_header_mode'] = 'x-rapidapi-key'
    else:
        # Highlightly direct deployments have also accepted the RapidAPI-style key header in practice.
        headers = {'x-rapidapi-key': key}
        result['auth_header_mode'] = 'x-rapidapi-key'
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [datetime.now(UTC).date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, f'{base}/matches', headers=headers, params={'date': d, 'limit': 100, 'timezone': 'Etc/UTC'})
        rows.extend(extract_rows(payload))
    contexts = matched_contexts(rows, targets, 'highlightly')
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors, 'matches_seen': len(rows), 'samples': rows[:3], 'matched_contexts': contexts})
    result['matched_targets'] = len(contexts)
    # Odds are often unavailable on the basic Highlightly plan. Probe once only when a match was found.
    if contexts and budget.can() and os.getenv('HIGHLIGHTLY_PROBE_ODDS', 'true').lower() in {'1', 'true', 'yes', 'on'}:
        event_id = contexts[0].get('provider_event_id')
        if event_id:
            odds_payload = await budget.get(client, f'{base}/odds', headers=headers, params={'matchId': event_id})
            result['odds_probe_rows'] = len(extract_rows(odds_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


async def probe_allsportsapi(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('ALLSPORTSAPI_API_KEY')
    budget = ProbeBudget('allsportsapi', as_int(os.getenv('ALLSPORTSAPI_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'allsportsapi', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'odds_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('ALLSPORTSAPI_BASE_URL') or 'https://apiv2.allsportsapi.com/football/').rstrip('/') + '/'
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [datetime.now(UTC).date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, base, params={'met': 'Fixtures', 'APIkey': key, 'from': d, 'to': d, 'timezone': 'UTC'})
        rows.extend(extract_rows(payload.get('result') if isinstance(payload, dict) else payload))
    contexts = matched_contexts(rows, targets, 'allsportsapi')
    result['fixtures_seen'] = len(rows); result['matched_targets'] = len(contexts); result['matched_contexts'] = contexts; result['samples'] = rows[:3]
    if rows and budget.can():
        event_id = str(rows[0].get('event_key') or rows[0].get('match_id') or '')
        if event_id:
            odds_payload = await budget.get(client, base, params={'met': 'Odds', 'APIkey': key, 'matchId': event_id})
            result['odds_probe_rows'] = len(extract_rows(odds_payload.get('result') if isinstance(odds_payload, dict) else odds_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


async def probe_api_football(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('API_FOOTBALL_KEY') or os.getenv('API_FOOTBALL_API_KEY') or os.getenv('RAPIDAPI_KEY')
    budget = ProbeBudget('api_football', as_int(os.getenv('API_FOOTBALL_PROBE_MAX_REQUESTS'), 4))
    result = {'provider': 'api_football', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'statistics_probe_rows': 0, 'lineups_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('API_FOOTBALL_BASE_URL') or 'https://v3.football.api-sports.io').rstrip('/')
    headers = {'x-apisports-key': key}
    if os.getenv('API_FOOTBALL_RAPIDAPI_HOST'):
        headers = {'x-rapidapi-key': key, 'x-rapidapi-host': os.getenv('API_FOOTBALL_RAPIDAPI_HOST') or 'api-football-v1.p.rapidapi.com'}
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [datetime.now(UTC).date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, f'{base}/fixtures', headers=headers, params={'date': d})
        rows.extend(extract_rows(payload))
    contexts = matched_contexts(rows, targets, 'api_football')
    result['fixtures_seen'] = len(rows); result['matched_targets'] = len(contexts); result['matched_contexts'] = contexts; result['samples'] = rows[:3]
    fixture_id = ''
    if rows:
        fixture = rows[0].get('fixture') if isinstance(rows[0].get('fixture'), dict) else {}
        fixture_id = str(fixture.get('id') or rows[0].get('fixture_id') or '')
    if fixture_id and budget.can():
        stats_payload = await budget.get(client, f'{base}/fixtures/statistics', headers=headers, params={'fixture': fixture_id})
        result['statistics_probe_rows'] = len(extract_rows(stats_payload))
    if fixture_id and budget.can():
        lineup_payload = await budget.get(client, f'{base}/fixtures/lineups', headers=headers, params={'fixture': fixture_id})
        result['lineups_probe_rows'] = len(extract_rows(lineup_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


def current_run_id() -> str:
    return str(os.getenv('GITHUB_RUN_ID') or os.getenv('RUN_ID') or '').strip()


def reusable_payload() -> dict[str, Any] | None:
    if os.getenv('SECONDARY_PROVIDER_PROBE_REUSE_SAME_RUN', 'true').lower() not in {'1', 'true', 'yes', 'on'}:
        return None
    run_id = current_run_id()
    payload = load_json(RUN_CACHE, {})
    if run_id and isinstance(payload, dict) and str(payload.get('github_run_id') or '') == run_id:
        write_json(OUT, payload)
        return payload
    return None


async def main_async() -> int:
    if os.getenv('EXTERNAL_SIGNAL_PROBES_ENABLED', 'true').lower() not in {'1', 'true', 'yes', 'on', 'force'}:
        write_json(OUT, {'status': 'disabled'})
        return 0
    cached = reusable_payload()
    if cached is not None:
        print(json.dumps(cached, ensure_ascii=False, indent=2, sort_keys=True))
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
        'github_run_id': current_run_id(),
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_count': len(targets),
        'target_sample': [
            {'match_key': target_match_key(t), 'home': metric(t, 'home_team', 'home'), 'away': metric(t, 'away_team', 'away'), 'kickoff': metric(t, 'commence_time', 'kickoff_utc', 'start_time')}
            for t in targets[:8]
        ],
        'providers': {row['provider']: row for row in results},
        'notes': [
            'Probe-only: no publication decisions are changed directly.',
            'All limits are per-run; any 429/5xx stops that provider for this probe only.',
            'Matched fixture rows can be merged as context evidence by merge_targeted_secondary_context.py.',
        ],
    }
    write_json(OUT, payload)
    CACHE.mkdir(parents=True, exist_ok=True)
    write_json(RUN_CACHE, payload)
    write_json(CACHE / f"probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main_async()))
