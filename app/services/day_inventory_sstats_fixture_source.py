from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.schemas import Match
from app.utils import canonicalize_league_name, canonicalize_team_name, is_low_tier_league, parse_datetime

UTC = timezone.utc
_INSTALLED = False
SSTATS_DIAG_PATH = Path('.data/exports/latest-day-inventory-sstats-fixture-summary.json')


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


def _dig(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _string(value: Any) -> str:
    return str(value or '').strip()


def _extract_team(row: dict[str, Any], side: str) -> str:
    team_obj = row.get(f'{side}Team') or row.get(f'{side}_team') or row.get(side)
    if isinstance(team_obj, dict):
        for key in ('name', 'title', 'shortName', 'short_name', 'displayName'):
            value = _string(team_obj.get(key))
            if value:
                return value
    elif isinstance(team_obj, str) and team_obj.strip() and not team_obj.strip().startswith('{'):
        return team_obj.strip()
    for key in (
        f'{side}Team',
        f'{side}_team',
        f'{side}TeamName',
        f'{side}_team_name',
        f'{side}Name',
        f'{side}_name',
        f'{side}Club',
        f'{side}_club',
        side,
    ):
        value = _string(row.get(key))
        if value and not value.startswith('{'):
            return value
    return ''


def _extract_league(row: dict[str, Any]) -> str:
    for path in (('season', 'league', 'name'), ('season', 'league', 'title'), ('league', 'name'), ('league', 'title'), ('competition', 'name')):
        value = _string(_dig(row, *path))
        if value:
            return value
    season_league = _dig(row, 'season', 'league')
    if isinstance(season_league, str) and season_league.strip():
        return season_league.strip()
    league_value = row.get('league')
    if isinstance(league_value, str) and league_value.strip():
        return league_value.strip()
    for key in ('leagueName', 'league_name', 'competitionName', 'tournamentName', 'competition', 'tournament'):
        value = _string(row.get(key))
        if value and not value.startswith('{'):
            return value
    return ''


def _extract_country(row: dict[str, Any]) -> str:
    for path in (('season', 'league', 'country'), ('league', 'country'), ('country', 'name')):
        value = _dig(row, *path)
        if isinstance(value, dict):
            value = value.get('name')
        text = _string(value)
        if text:
            return text
    return _string(row.get('country') or row.get('countryName'))


def _extract_start(row: dict[str, Any]) -> datetime | None:
    for key in ('dateUtc', 'dateUTC', 'utcDate', 'startTime', 'start_time', 'date', 'kickoff', 'kickoffUtc'):
        value = row.get(key)
        if value in (None, ''):
            continue
        try:
            return parse_datetime(str(value))
        except Exception:
            continue
    return None


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
                clean[str(key)] = {str(k): v for k, v in list(value.items())[:10] if isinstance(v, (str, int, float, bool)) or v is None}
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


def _row_parse_fail_reason(row: dict[str, Any], settings: Settings) -> str:
    home = _extract_team(row, 'home')
    away = _extract_team(row, 'away')
    league = _extract_league(row)
    start = _extract_start(row)
    if not home:
        return 'missing_home_team'
    if not away:
        return 'missing_away_team'
    if not league:
        return 'missing_league'
    if start is None:
        return 'missing_or_unparseable_start'
    if not bool(getattr(settings, 'allow_low_tier', False)) and is_low_tier_league(league):
        return 'low_tier_filtered'
    return 'unknown'


def _row_to_match(row: dict[str, Any], settings: Settings) -> Match | None:
    home = _extract_team(row, 'home')
    away = _extract_team(row, 'away')
    league = _extract_league(row)
    start = _extract_start(row)
    if not home or not away or not league or start is None:
        return None
    if not bool(getattr(settings, 'allow_low_tier', False)) and is_low_tier_league(league):
        return None
    source_event_id = _string(row.get('id') or row.get('flashId') or row.get('gameId') or row.get('eventId'))
    status_obj = row.get('status')
    status = _string(status_obj.get('name') if isinstance(status_obj, dict) else status_obj or row.get('statusName'))
    return Match(
        source='sstats',
        source_event_id=source_event_id,
        sport_key='soccer',
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=start,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league),
        tier='low' if is_low_tier_league(league) else 'mid',
        metadata={
            'provider': 'sstats',
            'country': _extract_country(row),
            'status': status,
            'flash_id': _string(row.get('flashId')),
            'season_id': _string(_dig(row, 'season', 'id')),
            'league_id': _string(_dig(row, 'season', 'league', 'id') or _dig(row, 'league', 'id')),
        },
    )


async def _fetch_sstats(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, Any]]:
    api_key = _string(os.getenv('SSTATS_API_KEY') or getattr(settings, 'sstats_api_key', ''))
    stats: dict[str, Any] = {
        'enabled': bool(api_key),
        'api_key_present': bool(api_key),
        'endpoint': '/Games/list',
        'requests': 0,
        'response_errors': 0,
        'http_statuses': [],
        'rows_fetched': 0,
        'rows_parsed_as_match': 0,
        'parse_fail_reasons': {},
        'matches_built': 0,
        'matches_for_target_local_date': 0,
        'rows_outside_target_local_date': 0,
        'duplicates_inside_sstats': 0,
        'low_tier_skipped': 0,
        'budget_exhausted': False,
        'last_error': None,
        'last_body_preview': None,
        'request_date': local_date,
    }
    preview: dict[str, Any] = {'sample_rows': [], 'sample_matches': [], 'parse_fail_examples': []}
    if not api_key:
        return [], {'stats': stats, 'preview': preview}

    target_day = date.fromisoformat(local_date)
    window_days = _env_int('DAY_INVENTORY_SSTATS_WINDOW_DAYS', 0, 0)
    date_from = (target_day - timedelta(days=window_days)).isoformat()
    date_to = (target_day + timedelta(days=window_days)).isoformat()
    limit = _env_int('DAY_INVENTORY_SSTATS_LIMIT', 1000, 100)
    max_requests = _env_int('DAY_INVENTORY_SSTATS_MAX_REQUESTS', 3, 1)
    offset = 0
    rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=float(getattr(settings, 'sstats_timeout_seconds', 20.0) or 20.0)) as client:
        while int(stats['requests']) < max_requests:
            stats['requests'] = int(stats['requests']) + 1
            try:
                response = await client.get(
                    'https://api.sstats.net/Games/list',
                    params={'from': date_from, 'to': date_to, 'limit': limit, 'offset': offset, 'apikey': api_key},
                )
            except Exception as exc:
                stats['response_errors'] = int(stats['response_errors']) + 1
                stats['last_error'] = f'{type(exc).__name__}: {exc}'
                break
            stats['http_statuses'].append(int(response.status_code))
            stats['last_body_preview'] = response.text[:1800]
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
            data = (payload.get('data') or payload.get('results')) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
            if not isinstance(data, list) or not data:
                break
            batch = [row for row in data if isinstance(row, dict)]
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)
    if int(stats['requests']) >= max_requests and rows:
        stats['budget_exhausted'] = True

    stats['rows_fetched'] = len(rows)
    preview['sample_rows'] = _safe_preview(rows, 3)
    matches: list[Match] = []
    seen: set[str] = set()
    for row in rows:
        match = _row_to_match(row, settings)
        if match is None:
            reason = _row_parse_fail_reason(row, settings)
            fail_map = dict(stats.get('parse_fail_reasons') or {})
            fail_map[reason] = int(fail_map.get(reason) or 0) + 1
            stats['parse_fail_reasons'] = fail_map
            if reason == 'low_tier_filtered':
                stats['low_tier_skipped'] = int(stats['low_tier_skipped']) + 1
            if len(preview['parse_fail_examples']) < 8:
                preview['parse_fail_examples'].append({'reason': reason, 'row': _safe_preview([row], 1)[0]})
            continue
        stats['rows_parsed_as_match'] = int(stats['rows_parsed_as_match']) + 1
        if _store_local_date(settings, match.commence_time) != local_date:
            stats['rows_outside_target_local_date'] = int(stats['rows_outside_target_local_date']) + 1
            continue
        if match.match_key in seen:
            stats['duplicates_inside_sstats'] = int(stats['duplicates_inside_sstats']) + 1
            continue
        seen.add(match.match_key)
        matches.append(match)
    stats['matches_built'] = len(matches)
    stats['matches_for_target_local_date'] = len(matches)
    preview['sample_matches'] = _match_preview(matches, 5)
    return matches, {'stats': stats, 'preview': preview}


def _write_compact_diag(local_date: str, sstats_meta: dict[str, Any], base_count: int, merged_count: int, added_raw: int) -> dict[str, Any]:
    stats = dict(sstats_meta.get('stats') or {})
    preview = dict(sstats_meta.get('preview') or {})
    compact = {
        'provider': 'sstats',
        'date_local': local_date,
        'base_matches_before_sstats': base_count,
        'sstats_matches_added_raw': added_raw,
        'matches_after_sstats_deduped': merged_count,
        'stats': {
            'enabled': stats.get('enabled'),
            'api_key_present': stats.get('api_key_present'),
            'endpoint': stats.get('endpoint'),
            'request_date': stats.get('request_date'),
            'requests': stats.get('requests'),
            'http_statuses': stats.get('http_statuses'),
            'response_errors': stats.get('response_errors'),
            'rows_fetched': stats.get('rows_fetched'),
            'rows_parsed_as_match': stats.get('rows_parsed_as_match'),
            'matches_built': stats.get('matches_built'),
            'matches_for_target_local_date': stats.get('matches_for_target_local_date'),
            'rows_outside_target_local_date': stats.get('rows_outside_target_local_date'),
            'duplicates_inside_sstats': stats.get('duplicates_inside_sstats'),
            'low_tier_skipped': stats.get('low_tier_skipped'),
            'budget_exhausted': stats.get('budget_exhausted'),
            'parse_fail_reasons': stats.get('parse_fail_reasons'),
            'last_error': stats.get('last_error'),
            'last_body_preview': stats.get('last_body_preview'),
        },
        'sample_rows': preview.get('sample_rows') or [],
        'sample_matches': preview.get('sample_matches') or [],
        'parse_fail_examples': preview.get('parse_fail_examples') or [],
    }
    try:
        SSTATS_DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SSTATS_DIAG_PATH.write_text(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass
    try:
        print('[day-inventory-sstats] ' + json.dumps(compact, ensure_ascii=False, sort_keys=True), flush=True)
    except Exception:
        pass
    return compact


def _source_rank(source: str) -> int:
    order = {
        'odds_api_io': 0,
        'football_data': 1,
        'bzzoiro': 2,
        'sstats': 3,
        'sportlogic': 4,
        'allsportsapi': 5,
        'thesportsdb': 6,
    }
    return order.get(str(source or '').strip().lower(), 20)


def _dedupe(matches: list[Match]) -> list[Match]:
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
                home_team_norm=match.home_team_norm,
                away_team_norm=match.away_team_norm,
                league_key=match.league_key,
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
            league_name=current.league_name,
            home_team=current.home_team,
            away_team=current.away_team,
            commence_time=current.commence_time,
            home_team_norm=current.home_team_norm,
            away_team_norm=current.away_team_norm,
            league_key=current.league_key,
            tier=current.tier,
            metadata=metadata,
        )
    return sorted(merged.values(), key=lambda item: (item.commence_time.isoformat(), item.league_name, item.home_team, item.away_team))


def _merge_meta(base_meta: Any, sstats_meta: dict[str, Any], base_count: int, merged_count: int, added_raw: int) -> dict[str, Any]:
    meta = dict(base_meta or {}) if isinstance(base_meta, dict) else {}
    attempts = dict(meta.get('attempts') or {})
    attempts['sstats'] = {'stats': dict(sstats_meta.get('stats') or {}), 'preview': dict(sstats_meta.get('preview') or {})}
    stats = dict(meta.get('stats') or {})
    stats['sstats_fixture_source'] = dict(sstats_meta.get('stats') or {})
    stats['matches_before_sstats_fixture_source'] = base_count
    stats['sstats_fixture_matches_added_raw'] = added_raw
    stats['matches_after_sstats_fixture_source_deduped'] = merged_count
    preview = dict(meta.get('preview') or {})
    preview['sstats_fixture_source'] = dict(sstats_meta.get('preview') or {})
    provider_name = str(meta.get('provider') or 'day_inventory')
    if 'sstats' not in provider_name:
        provider_name += '+sstats'
    meta['provider'] = provider_name
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
    if not _truthy(os.getenv('DAY_INVENTORY_ENABLE_SSTATS'), True):
        return {'status': 'disabled'}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    original = getattr(PredictionRunner, '_fetch_matches', None)
    if not callable(original):
        return {'status': 'missing_fetch_matches'}
    if getattr(PredictionRunner, '_harizon_day_inventory_sstats_fixture_patch', False):
        return {'status': 'already_patched'}

    async def patched_fetch_matches(self):  # type: ignore[no-untyped-def]
        base_matches, base_meta = await original(self)
        settings = getattr(self, 'settings', None) or Settings()
        local_date = _target_local_date(settings)
        sstats_matches, sstats_meta = await _fetch_sstats(settings, local_date)
        combined = _dedupe([*list(base_matches or []), *sstats_matches])
        _write_compact_diag(local_date, sstats_meta, len(base_matches or []), len(combined), len(sstats_matches))
        merged_meta = _merge_meta(base_meta, sstats_meta, len(base_matches or []), len(combined), len(sstats_matches))
        return combined, merged_meta

    PredictionRunner._fetch_matches = patched_fetch_matches
    PredictionRunner._harizon_day_inventory_sstats_fixture_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'patched': 'PredictionRunner._fetch_matches'}
