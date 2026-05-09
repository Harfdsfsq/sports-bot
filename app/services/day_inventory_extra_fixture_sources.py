from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.providers.sportlogic_provider import SportLogicProvider
from app.schemas import Match
from app.utils import canonicalize_league_name, canonicalize_team_name, is_low_tier_league, parse_datetime

UTC = timezone.utc
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _is_day_inventory_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else '').replace('\\', '/')
    return argv0.endswith('scripts/build_day_inventory.py') or argv0.endswith('build_day_inventory.py')


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return max(minimum, default)
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, default)


def _app_tz(settings: Settings):
    try:
        return ZoneInfo(str(getattr(settings, 'app_timezone', '') or 'Europe/Moscow'))
    except Exception:
        return UTC


def _target_local_date(settings: Settings) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(_app_tz(settings)).date().isoformat()


def _store_local_date(settings: Settings, value: datetime) -> str:
    return value.astimezone(_app_tz(settings)).date().isoformat()


def _safe_preview(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        clean: dict[str, Any] = {}
        for key, value in (row or {}).items():
            low = str(key).lower()
            if any(token in low for token in ('key', 'token', 'secret', 'authorization', 'apikey', 'api_key')):
                clean[str(key)] = '***'
            elif isinstance(value, (str, int, float, bool)) or value is None:
                clean[str(key)] = value
            elif isinstance(value, dict):
                nested: dict[str, Any] = {}
                for sub_key, sub_value in list(value.items())[:12]:
                    sub_low = str(sub_key).lower()
                    nested[str(sub_key)] = '***' if any(token in sub_low for token in ('key', 'token', 'secret')) else sub_value
                clean[str(key)] = nested
            else:
                clean[str(key)] = type(value).__name__
        out.append(clean)
    return out


def _match_preview(matches: list[Match], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            'match_key': item.match_key,
            'source': item.source,
            'league_name': item.league_name,
            'home_team': item.home_team,
            'away_team': item.away_team,
            'commence_time': item.commence_time.isoformat(),
            'tier': item.tier,
        }
        for item in matches[:limit]
    ]


def _source_rank(source: str) -> int:
    order = {
        'odds_api_io': 0,
        'football_data': 1,
        'bzzoiro': 2,
        'sportlogic': 3,
        'allsportsapi': 4,
        'thesportsdb': 5,
    }
    return order.get(str(source or '').strip().lower(), 20)


def _dedupe_matches(matches: list[Match]) -> list[Match]:
    merged: dict[str, Match] = {}
    for match in sorted(matches or [], key=lambda item: _source_rank(item.source)):
        current = merged.get(match.match_key)
        if current is None:
            metadata = dict(match.metadata or {})
            metadata['sources_seen'] = str(match.source or '')
            if match.source_event_id:
                metadata['provider_source_ids'] = {str(match.source): str(match.source_event_id)}
            merged[match.match_key] = Match(
                source=match.source,
                source_event_id=match.source_event_id,
                sport_key=match.sport_key,
                league_name=match.league_name,
                home_team=match.home_team,
                away_team=match.away_team,
                commence_time=match.commence_time,
                home_team_norm=match.home_team_norm or canonicalize_team_name(match.home_team),
                away_team_norm=match.away_team_norm or canonicalize_team_name(match.away_team),
                league_key=match.league_key or canonicalize_league_name(match.league_name),
                tier=match.tier,
                metadata=metadata,
            )
            continue
        metadata = dict(current.metadata or {})
        sources = {item.strip() for item in str(metadata.get('sources_seen') or '').split(',') if item.strip()}
        sources.add(str(match.source or ''))
        source_ids = dict(metadata.get('provider_source_ids') or {})
        if current.source_event_id:
            source_ids.setdefault(str(current.source), str(current.source_event_id))
        if match.source_event_id:
            source_ids[str(match.source)] = str(match.source_event_id)
        metadata['sources_seen'] = ','.join(sorted(sources))
        metadata['provider_source_ids'] = source_ids
        merged[match.match_key] = Match(
            source=current.source,
            source_event_id=current.source_event_id,
            sport_key=current.sport_key,
            league_name=current.league_name or match.league_name,
            home_team=current.home_team or match.home_team,
            away_team=current.away_team or match.away_team,
            commence_time=current.commence_time,
            home_team_norm=current.home_team_norm or canonicalize_team_name(current.home_team),
            away_team_norm=current.away_team_norm or canonicalize_team_name(current.away_team),
            league_key=current.league_key or canonicalize_league_name(current.league_name),
            tier=current.tier if current.tier != 'low' else match.tier,
            metadata=metadata,
        )
    return sorted(merged.values(), key=lambda item: (item.commence_time.isoformat(), item.league_name, item.home_team, item.away_team))


def _extract_bzz_team(row: dict[str, Any], side: str) -> str:
    value = str(row.get(f'{side}_team') or '').strip()
    if value:
        return value
    obj = row.get(f'{side}_team_obj')
    if isinstance(obj, dict):
        return str(obj.get('name') or obj.get('short_name') or '').strip()
    return ''


def _extract_bzz_league(row: dict[str, Any]) -> str:
    league = row.get('league')
    if isinstance(league, dict):
        return str(league.get('name') or league.get('title') or '').strip()
    return str(league or '').strip()


def _bzz_row_to_match(row: dict[str, Any], settings: Settings) -> Match | None:
    home = _extract_bzz_team(row, 'home')
    away = _extract_bzz_team(row, 'away')
    league = _extract_bzz_league(row)
    if not home or not away or not league:
        return None
    if not bool(getattr(settings, 'allow_low_tier', False)) and is_low_tier_league(league):
        return None
    raw_dt = row.get('event_date') or row.get('date') or row.get('start_time')
    try:
        commence = parse_datetime(raw_dt)
    except Exception:
        return None
    country = row.get('country')
    country_name = str(country.get('name') if isinstance(country, dict) else country or '').strip()
    return Match(
        source='bzzoiro',
        source_event_id=str(row.get('id') or row.get('event_id') or ''),
        sport_key='soccer',
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=commence,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league),
        tier='low' if is_low_tier_league(league) else 'mid',
        metadata={
            'provider': 'bzzoiro',
            'status': str(row.get('status') or '').strip(),
            'period': str(row.get('period') or '').strip(),
            'venue': str(row.get('venue') or row.get('stadium') or '').strip(),
            'country': country_name,
        },
    )


async def _fetch_bzzoiro(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {
        'enabled': True,
        'api_key_present': bool(os.getenv('BZZOIRO_API_KEY') or getattr(settings, 'bzzoiro_api_key', None)),
        'requests': 0,
        'response_errors': 0,
        'http_statuses': [],
        'pages_requested': 0,
        'events_fetched': 0,
        'matches_built': 0,
        'matches_for_target_local_date': 0,
        'low_tier_skipped': 0,
        'last_error': None,
        'last_body_preview': None,
    }
    preview: dict[str, Any] = {'sample_events': [], 'sample_matches': []}
    api_key = str(os.getenv('BZZOIRO_API_KEY') or getattr(settings, 'bzzoiro_api_key', '') or '').strip()
    if not api_key:
        stats['enabled'] = False
        return [], {'stats': stats, 'preview': preview}

    target_day = date.fromisoformat(local_date)
    window_days = _env_int('DAY_INVENTORY_BZZOIRO_WINDOW_DAYS', 1, 0)
    date_from = (target_day - timedelta(days=window_days)).isoformat()
    date_to = (target_day + timedelta(days=window_days)).isoformat()
    max_pages = _env_int('DAY_INVENTORY_BZZOIRO_MAX_PAGES', 10, 1)
    max_requests = _env_int('DAY_INVENTORY_BZZOIRO_MAX_REQUESTS', 12, 1)
    rows: list[dict[str, Any]] = []
    headers = {'Authorization': f'Token {api_key}'}

    async with httpx.AsyncClient(timeout=float(getattr(settings, 'bzzoiro_timeout_seconds', 20.0) or 20.0)) as client:
        page = 1
        while page <= max_pages and int(stats['requests']) < max_requests:
            stats['requests'] = int(stats['requests']) + 1
            stats['pages_requested'] = int(stats['pages_requested']) + 1
            try:
                response = await client.get(
                    'https://sports.bzzoiro.com/api/events/',
                    headers=headers,
                    params={'date_from': date_from, 'date_to': date_to, 'tz': 'UTC', 'page': page},
                )
            except Exception as exc:
                stats['response_errors'] = int(stats['response_errors']) + 1
                stats['last_error'] = f'{type(exc).__name__}: {exc}'
                break
            stats['http_statuses'].append(int(response.status_code))
            stats['last_body_preview'] = response.text[:1500]
            if response.status_code != 200:
                stats['response_errors'] = int(stats['response_errors']) + 1
                stats['last_error'] = f'http_status={response.status_code}'
                break
            try:
                payload = response.json()
            except Exception as exc:
                stats['response_errors'] = int(stats['response_errors']) + 1
                stats['last_error'] = f'json:{type(exc).__name__}: {exc}'
                break
            batch = payload.get('results') if isinstance(payload, dict) else payload if isinstance(payload, list) else []
            if not isinstance(batch, list) or not batch:
                break
            rows.extend([item for item in batch if isinstance(item, dict)])
            if not isinstance(payload, dict) or not payload.get('next'):
                break
            page += 1

    stats['events_fetched'] = len(rows)
    preview['sample_events'] = _safe_preview(rows, 3)
    matches: list[Match] = []
    for row in rows:
        match = _bzz_row_to_match(row, settings)
        if match is None:
            league = _extract_bzz_league(row)
            if league and is_low_tier_league(league):
                stats['low_tier_skipped'] = int(stats['low_tier_skipped']) + 1
            continue
        if _store_local_date(settings, match.commence_time) == local_date:
            matches.append(match)
    stats['matches_built'] = len(matches)
    stats['matches_for_target_local_date'] = len(matches)
    preview['sample_matches'] = _match_preview(matches, 5)
    return matches, {'stats': stats, 'preview': preview}


def _allsportsapi_row_to_match(row: dict[str, Any], settings: Settings) -> Match | None:
    home = str(row.get('event_home_team') or '').strip()
    away = str(row.get('event_away_team') or '').strip()
    league = str(row.get('league_name') or '').strip()
    if not home or not away or not league:
        return None
    if not bool(getattr(settings, 'allow_low_tier', False)) and is_low_tier_league(league):
        return None
    raw_date = str(row.get('event_date') or '').strip()
    raw_time = str(row.get('event_time') or '').strip() or '12:00'
    if not raw_date:
        return None
    try:
        time_value = raw_time if len(raw_time.split(':')) == 3 else f'{raw_time}:00'
        commence = parse_datetime(f'{raw_date}T{time_value}+00:00')
    except Exception:
        return None
    return Match(
        source='allsportsapi',
        source_event_id=str(row.get('event_key') or row.get('match_id') or ''),
        sport_key='soccer',
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=commence,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league),
        tier='low' if is_low_tier_league(league) else 'mid',
        metadata={
            'provider': 'allsportsapi',
            'country': str(row.get('country_name') or '').strip(),
            'round': str(row.get('league_round') or '').strip(),
            'season': str(row.get('league_season') or '').strip(),
            'venue': str(row.get('event_stadium') or '').strip(),
            'status': str(row.get('event_status') or '').strip(),
        },
    )


async def _fetch_allsportsapi(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {
        'enabled': True,
        'api_key_present': bool(os.getenv('ALLSPORTSAPI_API_KEY') or os.getenv('ALLSPORTSAPI_KEY') or getattr(settings, 'allsportsapi_api_key', None)),
        'requests': 0,
        'response_errors': 0,
        'http_statuses': [],
        'fixtures_fetched': 0,
        'matches_built': 0,
        'matches_for_target_local_date': 0,
        'low_tier_skipped': 0,
        'last_error': None,
        'last_body_preview': None,
        'request_date': local_date,
    }
    preview: dict[str, Any] = {'sample_fixtures': [], 'sample_matches': []}
    api_key = str(os.getenv('ALLSPORTSAPI_API_KEY') or os.getenv('ALLSPORTSAPI_KEY') or getattr(settings, 'allsportsapi_api_key', '') or '').strip()
    if not api_key:
        stats['enabled'] = False
        return [], {'stats': stats, 'preview': preview}
    base_url = str(getattr(settings, 'allsportsapi_base_url', 'https://apiv2.allsportsapi.com/football/') or 'https://apiv2.allsportsapi.com/football/').rstrip('/')
    window_days = _env_int('DAY_INVENTORY_ALLSPORTSAPI_WINDOW_DAYS', 0, 0)
    target_day = date.fromisoformat(local_date)
    request_from = (target_day - timedelta(days=window_days)).isoformat()
    request_to = (target_day + timedelta(days=window_days)).isoformat()
    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=float(getattr(settings, 'allsportsapi_timeout_seconds', 12.0) or 12.0)) as client:
            stats['requests'] = 1
            response = await client.get(
                f'{base_url}/',
                params={'met': 'Fixtures', 'APIkey': api_key, 'from': request_from, 'to': request_to, 'timezone': 'UTC'},
            )
    except Exception as exc:
        stats['response_errors'] = 1
        stats['last_error'] = f'{type(exc).__name__}: {exc}'
        return [], {'stats': stats, 'preview': preview}
    stats['http_statuses'].append(int(response.status_code))
    stats['last_body_preview'] = response.text[:1800]
    if response.status_code != 200:
        stats['response_errors'] = 1
        stats['last_error'] = f'http_status={response.status_code}'
        return [], {'stats': stats, 'preview': preview}
    try:
        payload = response.json()
    except Exception as exc:
        stats['response_errors'] = 1
        stats['last_error'] = f'json:{type(exc).__name__}: {exc}'
        return [], {'stats': stats, 'preview': preview}
    result = payload.get('result') if isinstance(payload, dict) else []
    if isinstance(result, list):
        rows = [row for row in result if isinstance(row, dict)]
    stats['fixtures_fetched'] = len(rows)
    preview['sample_fixtures'] = _safe_preview(rows, 3)
    matches: list[Match] = []
    for row in rows:
        match = _allsportsapi_row_to_match(row, settings)
        if match is None:
            league = str(row.get('league_name') or '').strip()
            if league and is_low_tier_league(league):
                stats['low_tier_skipped'] = int(stats['low_tier_skipped']) + 1
            continue
        if _store_local_date(settings, match.commence_time) == local_date:
            matches.append(match)
    max_matches = _env_int('DAY_INVENTORY_ALLSPORTSAPI_MATCH_LIMIT', 500, 1)
    matches = matches[:max_matches]
    stats['matches_built'] = len(matches)
    stats['matches_for_target_local_date'] = len(matches)
    preview['sample_matches'] = _match_preview(matches, 5)
    return matches, {'stats': stats, 'preview': preview}


async def _fetch_provider_matches(provider_name: str, provider: Any, settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    stats: dict[str, Any] = {'enabled': True, 'requests': 0, 'response_errors': 0, 'matches_built': 0, 'matches_for_target_local_date': 0, 'last_error': None}
    preview: dict[str, Any] = {'sample_matches': []}
    try:
        matches, provider_stats, provider_preview = await provider.fetch_matches()
        if isinstance(provider_stats, dict):
            stats.update(provider_stats)
        if isinstance(provider_preview, dict):
            preview.update(provider_preview)
    except Exception as exc:
        stats['response_errors'] = int(stats.get('response_errors') or 0) + 1
        stats['last_error'] = f'{type(exc).__name__}: {exc}'
        return [], {'stats': stats, 'preview': preview}
    matches_for_day = [match for match in matches if _store_local_date(settings, match.commence_time) == local_date]
    stats['matches_for_target_local_date'] = len(matches_for_day)
    stats['matches_built'] = len(matches_for_day)
    preview['sample_matches'] = _match_preview(matches_for_day, 5)
    return matches_for_day, {'stats': stats, 'preview': preview}


async def _fetch_sportlogic(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    provider = SportLogicProvider(settings)
    provider.match_limit = _env_int('DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT', 500, 1)
    provider.max_requests_per_run = _env_int('DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS', 12, 1)
    return await _fetch_provider_matches('sportlogic', provider, settings, local_date)


async def _capture(name: str, coro: Any) -> tuple[str, list[Match], dict[str, Any]]:
    try:
        matches, meta = await coro
        return name, matches, meta
    except Exception as exc:
        return name, [], {'stats': {'enabled': True, 'response_errors': 1, 'last_error': f'{type(exc).__name__}: {exc}'}, 'preview': {}}


async def _fetch_extra_sources(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    providers: list[tuple[str, Any]] = []
    if _truthy(os.getenv('DAY_INVENTORY_ENABLE_BZZOIRO'), True):
        providers.append(('bzzoiro', _fetch_bzzoiro(settings, local_date)))
    if _truthy(os.getenv('DAY_INVENTORY_ENABLE_ALLSPORTSAPI'), True):
        providers.append(('allsportsapi', _fetch_allsportsapi(settings, local_date)))
    if _truthy(os.getenv('DAY_INVENTORY_ENABLE_SPORTLOGIC'), True):
        providers.append(('sportlogic', _fetch_sportlogic(settings, local_date)))

    results = await asyncio.gather(*[_capture(name, coro) for name, coro in providers]) if providers else []
    matches: list[Match] = []
    attempts: dict[str, Any] = {}
    stats: dict[str, Any] = {
        'mode': 'extra_fixture_sources',
        'providers_attempted': [name for name, _ in providers],
        'provider_day_match_counts': {},
        'matches_combined_raw': 0,
        'matches_combined_deduped': 0,
    }
    preview: dict[str, Any] = {}
    for name, provider_matches, meta in results:
        matches.extend(provider_matches)
        attempts[name] = {'stats': dict((meta or {}).get('stats') or {}), 'preview': dict((meta or {}).get('preview') or {})}
        preview[name] = dict((meta or {}).get('preview') or {})
        stats['provider_day_match_counts'][name] = len(provider_matches)
    deduped = _dedupe_matches(matches)
    stats['matches_combined_raw'] = len(matches)
    stats['matches_combined_deduped'] = len(deduped)
    return deduped, {'provider': 'extra_fixture_sources', 'attempts': attempts, 'stats': stats, 'preview': preview}


def _merge_meta(original_meta: Any, extra_meta: dict[str, Any], original_count: int, merged_count: int, extra_count: int) -> dict[str, Any]:
    meta = dict(original_meta or {}) if isinstance(original_meta, dict) else {}
    attempts = dict(meta.get('attempts') or {})
    attempts.update(dict(extra_meta.get('attempts') or {}))
    stats = dict(meta.get('stats') or {})
    stats['extra_fixture_sources'] = dict(extra_meta.get('stats') or {})
    stats['matches_before_extra_sources'] = original_count
    stats['extra_fixture_matches_added_raw'] = extra_count
    stats['matches_after_extra_sources_deduped'] = merged_count
    preview = dict(meta.get('preview') or {})
    preview['extra_fixture_sources'] = dict(extra_meta.get('preview') or {})
    meta['provider'] = str(meta.get('provider') or 'odds_api_io') + '+extra_fixture_sources'
    meta['attempts'] = attempts
    meta['stats'] = stats
    meta['preview'] = preview
    return meta


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _is_day_inventory_process():
        return {'status': 'skipped_not_day_inventory_process'}
    if not _truthy(os.getenv('DAY_INVENTORY_EXTRA_FIXTURES_ENABLED'), True):
        return {'status': 'disabled'}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    original = getattr(PredictionRunner, '_fetch_matches', None)
    if not callable(original):
        return {'status': 'missing_fetch_matches'}
    if getattr(PredictionRunner, '_harizon_day_inventory_extra_sources_patch', False):
        return {'status': 'already_patched'}

    async def patched_fetch_matches(self):  # type: ignore[no-untyped-def]
        base_matches, base_meta = await original(self)
        settings = getattr(self, 'settings', None) or Settings()
        local_date = _target_local_date(settings)
        extra_matches, extra_meta = await _fetch_extra_sources(settings, local_date)
        combined = _dedupe_matches([*list(base_matches or []), *extra_matches])
        merged_meta = _merge_meta(base_meta, extra_meta, len(base_matches or []), len(combined), len(extra_matches))
        return combined, merged_meta

    PredictionRunner._fetch_matches = patched_fetch_matches
    PredictionRunner._harizon_day_inventory_extra_sources_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'patched': 'PredictionRunner._fetch_matches'}
