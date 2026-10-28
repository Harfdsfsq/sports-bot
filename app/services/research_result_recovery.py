"""Bounded result recovery for research only; never reads the betting ledger."""
from __future__ import annotations

from collections import defaultdict
import asyncio
from datetime import date, timedelta

from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time
from app.services.fixture_identity import same_team_identity


def matching_fixture(result, forecast):
    """Names, squad and kickoff must agree even when a provider ID is known."""
    a, b = parse_time(result.get('kickoff')), parse_time(forecast.get('kickoff'))
    return bool(a and b and abs(a-b) <= timedelta(minutes=15)
                and same_team_identity(forecast.get('home'), [result.get('home')])
                and same_team_identity(forecast.get('away'), [result.get('away')]))


def overdue_forecasts(history, now):
    from app.services.goal_research import unique_forecasts
    return [f for f in unique_forecasts(history.data['forecasts'])
            if f.get('status') == 'pending' and parse_time(f.get('kickoff'))
            and timedelta(hours=6) <= now - parse_time(f['kickoff']) <= timedelta(days=30)]


def bind_source_ids(history, forecasts):
    """Recover legacy IDs only from a unique exact fixture binding."""
    index = defaultdict(list)
    for key, row in history.data.get('fixture_ids', {}).items():
        if key.startswith('sstats:'):
            index[(row['home'], row['away'], row['kickoff'])].append(key.split(':', 1)[1])
    for f in forecasts:
        ids = index.get((f['home'], f['away'], f['kickoff']), [])
        if len(ids) == 1 and ids[0].isdecimal():
            f.setdefault('source_ids', {}).setdefault('sstats', ids[0])


def due(checks, key, now, hours=24):
    previous = checks.get(key) or {}
    if key.startswith('sstats:') and previous.get('parser_version') != 2:
        return True
    at = parse_time(previous.get('at'))
    if previous.get('status') == 'request_failed':
        hours = 6
    return at is None or now-at >= timedelta(hours=hours)


def relevant_rows(provider, rows, forecasts):
    from app.services.goal_research import normalize_result
    return [row for row in rows if isinstance(row, dict)
            and (result := normalize_result(provider, row)) is not None
            and any(matching_fixture(result, f) for f in forecasts)]


async def recover_alternative_results(history, settings, now):
    """At most 3 detail + 2 Football-data + 1 OpenLigaDB requests per run."""
    forecasts = overdue_forecasts(history, now)
    bind_source_ids(history, forecasts)
    checks = history.data.setdefault('result_endpoint_checks', {})
    summary = {'requests': 0, 'added': 0, 'checks': [], 'id_bound': sum(bool(f.get('source_ids')) for f in forecasts)}
    jobs = []
    if getattr(settings, 'sstats_api_key', None):
        targets = {}
        for f in forecasts:
            sid = str((f.get('source_ids') or {}).get('sstats') or '')
            # Repeating the same source cannot settle its own contradictory scores.
            if sid.isdecimal() and int(sid) > 0 and f.get('resolution_reason') != 'conflicting_result':
                targets.setdefault(sid, []).append(f)
        keys = sorted(targets, key=lambda sid: ((checks.get('sstats:'+sid) or {}).get('at', ''), sid))
        for sid in [sid for sid in keys if due(checks, 'sstats:'+sid, now)][:3]:
            jobs.append(('sstats', 'sstats:'+sid, 'https://api.sstats.net/Games/'+sid,
                         {'apikey': str(settings.sstats_api_key)}, {'X-API-Key': str(settings.sstats_api_key)}, targets[sid]))
    if getattr(settings, 'football_data_api_key', None):
        dates = defaultdict(list)
        for f in forecasts:
            dates[parse_time(f['kickoff']).date().isoformat()].append(f)
        keys = sorted(dates, key=lambda day: ((checks.get('football_data:'+day) or {}).get('at', ''), day))
        for day in [day for day in keys if due(checks, 'football_data:'+day, now)][:2]:
            # dateFrom is inclusive and dateTo exclusive in Football-data v4.
            end = (date.fromisoformat(day)+timedelta(days=1)).isoformat()
            jobs.append(('football_data', 'football_data:'+day, 'https://api.football-data.org/v4/matches',
                         {'dateFrom': day, 'dateTo': end, 'status': 'FINISHED'},
                         {'X-Auth-Token': str(settings.football_data_api_key)}, dates[day]))
    if getattr(settings, 'enable_openligadb_context', True):
        from app.providers.openligadb import OPENLIGADB_DEFAULT_ALIASES
        from app.utils import canonicalize_league_name
        aliases = {canonicalize_league_name(name): slug for name, slug in OPENLIGADB_DEFAULT_ALIASES.items()}
        groups = defaultdict(list)
        for f in forecasts:
            name = canonicalize_league_name(str(f.get('competition_name') or ''))
            slug = aliases.get(name)
            if slug:
                kickoff = parse_time(f['kickoff'])
                season = kickoff.year if kickoff.month >= 7 else kickoff.year-1
                groups[(slug, season)].append(f)
        keys = sorted(groups, key=lambda item: ((checks.get('openligadb:'+str(item)) or {}).get('at', ''), item))
        for slug, season in [item for item in keys if due(checks, 'openligadb:'+str(item), now)][:1]:
            jobs.append(('openligadb', 'openligadb:'+str((slug, season)),
                         f'https://api.openligadb.de/getmatchdata/{slug}/{season}', {}, {}, groups[(slug, season)]))
    if not jobs:
        return summary
    blocked = set()
    async with BudgetedAsyncClient(timeout=6.0) as client:
        for provider, key, url, params, headers, targets in jobs:
            if provider in blocked:
                continue
            # Persist before I/O, including failures and quota refusals.
            checks[key] = {'at': now.isoformat(), 'status': 'attempted', 'parser_version': 2}
            history.save()
            probe = {'provider': provider, 'target': key, 'rows': 0, 'added': 0}
            summary['requests'] += 1
            try:
                response = await asyncio.wait_for(client.get(url, params=params, headers=headers), timeout=7)
                probe['http_status'] = response.status_code
                if response.status_code in {401, 403, 429}:
                    blocked.add(provider)
                response.raise_for_status()
                payload = response.json()
                if provider == 'sstats':
                    row = payload.get('data', payload) if isinstance(payload, dict) else None
                    # /Games/{id} wraps the game in data.game, unlike /Games/list.
                    if isinstance(row, dict) and isinstance(row.get('game'), dict):
                        row = row['game']
                    rows = [row] if isinstance(row, dict) and str(row.get('id')) == key.split(':', 1)[1] else []
                    if rows:
                        probe['game_status'] = str(row.get('statusName') or 'unknown')
                    if not rows:
                        probe['response_issue'] = 'unexpected_game_id_or_shape'
                elif provider == 'football_data':
                    rows = payload.get('matches', []) if isinstance(payload, dict) else []
                else:
                    # Cup finals may include ET. Require explicit regulation time.
                    rows = [row for row in payload if isinstance(row, dict)
                            and any(r.get('resultTypeKind') == 'After90Minutes' for r in row.get('matchResults') or [])] if isinstance(payload, list) else []
                from app.services.goal_research import normalize_result
                rows = rows if isinstance(rows, list) else []
                accepted = relevant_rows(provider, rows, targets)
                final_rows = sum(isinstance(row, dict) and normalize_result(provider, row) is not None for row in rows)
                probe.update(rows_returned=len(rows), finished_regulation_rows=final_rows,
                             identity_mismatches=final_rows-len(accepted))
                probe.update(rows=len(accepted), added=history.ingest(provider, accepted, now))
                summary['added'] += probe['added']
                checks[key].update(status='checked', rows=probe['rows'])
            except Exception as exc:
                # Only the type is exported; errors can contain credential URLs.
                probe['error_type'] = type(exc).__name__
                checks[key]['status'] = 'request_failed'
            summary['checks'].append(probe)
            history.save()
    return summary
