from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.schemas import Match
from app.services.day_inventory import DayInventoryStore
from app.services.runner import PredictionRunner
from app.utils import canonicalize_league_name, canonicalize_team_name, is_low_tier_league, parse_datetime

UTC = timezone.utc
ENV_BOOTSTRAP_KEY = 'MATCH_BOOTSTRAP_PROVIDER'


def app_tz(settings: Settings):
    try:
        return ZoneInfo(str(getattr(settings, 'app_timezone', '') or 'Europe/Moscow'))
    except Exception:
        return UTC


def target_local_date(settings: Settings) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz(settings)).date().isoformat()


def maybe_override_bootstrap_provider(settings: Settings) -> tuple[str | None, str | None, str | None]:
    original_setting = str(getattr(settings, 'match_bootstrap_provider', '') or '').strip() or None
    original_env = str(os.getenv(ENV_BOOTSTRAP_KEY, '') or '').strip() or None
    override = str(os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or '').strip() or None
    if override:
        if hasattr(settings, 'match_bootstrap_provider'):
            setattr(settings, 'match_bootstrap_provider', override)
        os.environ[ENV_BOOTSTRAP_KEY] = override
    return original_setting, original_env, override


def restore_bootstrap_provider(settings: Settings, original_setting: str | None, original_env: str | None) -> None:
    if hasattr(settings, 'match_bootstrap_provider'):
        setattr(settings, 'match_bootstrap_provider', original_setting or '')
    if original_env:
        os.environ[ENV_BOOTSTRAP_KEY] = original_env
    else:
        os.environ.pop(ENV_BOOTSTRAP_KEY, None)


async def fetch_inventory_matches(runner: PredictionRunner) -> tuple[list, dict]:
    bootstrap_matches, bootstrap_meta = await runner._fetch_matches()  # noqa: SLF001
    deduped_matches = runner._dedupe_matches(bootstrap_matches)  # noqa: SLF001
    return deduped_matches, bootstrap_meta


def _football_data_headers(settings: Settings) -> dict[str, str]:
    return {'X-Auth-Token': str(getattr(settings, 'football_data_api_key', '') or '')}


def _football_data_url(settings: Settings) -> str:
    return str(getattr(settings, 'football_data_base_url', 'https://api.football-data.org/v4') or 'https://api.football-data.org/v4').rstrip('/') + '/matches'


def _football_data_min_matches() -> int:
    try:
        return max(1, int(float(os.getenv('DAY_INVENTORY_DIRECT_MIN_MATCHES', '12'))))
    except Exception:
        return 12


def _football_data_window_days() -> int:
    try:
        return max(0, int(float(os.getenv('DAY_INVENTORY_DIRECT_WINDOW_DAYS', '1'))))
    except Exception:
        return 1


def _row_to_match(row: dict, settings: Settings) -> Match | None:
    home_team = row.get('homeTeam') or {}
    away_team = row.get('awayTeam') or {}
    competition = row.get('competition') or {}
    home = str(home_team.get('name') or '').strip()
    away = str(away_team.get('name') or '').strip()
    league_name = str(competition.get('name') or '').strip()
    if not home or not away or not league_name:
        return None
    try:
        commence_time = parse_datetime(str(row.get('utcDate') or ''))
    except Exception:
        return None
    if not bool(getattr(settings, 'allow_low_tier', False)) and is_low_tier_league(league_name):
        return None
    competition_ref = str((competition.get('code') or competition.get('id') or '')).strip()
    return Match(
        source='football_data',
        source_event_id=str(row.get('id') or competition_ref or ''),
        sport_key='soccer',
        league_name=league_name,
        home_team=home,
        away_team=away,
        commence_time=commence_time,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(league_name),
        tier='mid',
        metadata={
            'competition_code': str(competition.get('code') or '').strip(),
            'competition_type': str(competition.get('type') or '').strip(),
            'stage': str(row.get('stage') or '').strip(),
            'group': str(row.get('group') or '').strip(),
            'status': str(row.get('status') or '').strip(),
        },
    )


async def fetch_direct_football_data_inventory(settings: Settings, local_date: str) -> tuple[list[Match], dict[str, object]]:
    stats: dict[str, object] = {
        'enabled': True,
        'api_key_present': bool(getattr(settings, 'football_data_api_key', None)),
        'requests': 0,
        'response_errors': 0,
        'events_fetched': 0,
        'matches_built': 0,
        'matches_for_target_local_date': 0,
        'low_tier_skipped': 0,
        'http_statuses': [],
        'last_body_preview': None,
        'budget_exhausted': False,
        'rate_limited': False,
        'max_http_requests_per_run': 1,
        'request_date_from': None,
        'request_date_to': None,
    }
    preview: dict[str, object] = {'sample_events': [], 'sample_matches': []}
    if not stats['api_key_present']:
        raise RuntimeError('football_data_api_key_missing')

    target_day = date.fromisoformat(local_date)
    window_days = _football_data_window_days()
    request_from = (target_day - timedelta(days=window_days)).isoformat()
    request_to = (target_day + timedelta(days=window_days)).isoformat()
    stats['request_date_from'] = request_from
    stats['request_date_to'] = request_to

    async with httpx.AsyncClient(timeout=float(getattr(settings, 'football_data_timeout_seconds', 20.0) or 20.0), headers=_football_data_headers(settings)) as client:
        stats['requests'] = 1
        response = await client.get(
            _football_data_url(settings),
            params={
                'dateFrom': request_from,
                'dateTo': request_to,
                'status': 'SCHEDULED,TIMED',
                'limit': 200,
            },
        )
        stats['http_statuses'].append(int(response.status_code))
        stats['last_body_preview'] = response.text[:1800]
        if response.status_code == 429:
            stats['response_errors'] = 1
            stats['rate_limited'] = True
            raise RuntimeError('football_data_rate_limited')
        if response.status_code != 200:
            stats['response_errors'] = 1
            raise RuntimeError(f'football_data_http_{response.status_code}')
        payload = response.json()

    rows = payload.get('matches') if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    stats['events_fetched'] = len(rows)
    if rows:
        preview['sample_events'] = rows[:3]

    matches: list[Match] = []
    sample_matches: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        match = _row_to_match(row, settings)
        if match is None:
            if row.get('competition') and is_low_tier_league(str((row.get('competition') or {}).get('name') or '')):
                stats['low_tier_skipped'] = int(stats.get('low_tier_skipped') or 0) + 1
            continue
        matches.append(match)
        if len(sample_matches) < 5:
            sample_matches.append(
                {
                    'match_key': match.match_key,
                    'league_name': match.league_name,
                    'home_team': match.home_team,
                    'away_team': match.away_team,
                    'commence_time': match.commence_time.isoformat(),
                    'tier': match.tier,
                }
            )
    stats['matches_built'] = len(matches)
    preview['sample_matches'] = sample_matches

    matches_for_target = [match for match in matches if match.commence_time.astimezone(app_tz(settings)).date().isoformat() == local_date]
    stats['matches_for_target_local_date'] = len(matches_for_target)
    meta = {
        'provider': 'football_data',
        'attempts': {'football_data': {'stats': stats, 'preview': preview}},
        'stats': stats,
        'preview': preview,
    }
    return matches_for_target, meta


async def fetch_requested_bootstrap(settings: Settings, local_date: str, override_provider: str | None) -> tuple[list[Match], dict[str, object]] | None:
    provider = str(override_provider or '').strip().lower()
    if provider == 'football_data':
        matches, meta = await fetch_direct_football_data_inventory(settings, local_date)
        if len(matches) < _football_data_min_matches():
            raise RuntimeError(f'football_data_low_coverage:{len(matches)}<{_football_data_min_matches()}')
        return matches, meta
    return None


async def main_async() -> int:
    settings = Settings()
    store = DayInventoryStore(timezone_name=str(getattr(settings, 'app_timezone', 'Europe/Moscow') or 'Europe/Moscow'))
    local_date = target_local_date(settings)
    source_meta: dict[str, object] = {}

    original_setting, original_env, override_provider = maybe_override_bootstrap_provider(settings)
    runner = PredictionRunner(settings)
    matches: list[Match] = []

    try:
        try:
            direct_result = await fetch_requested_bootstrap(settings, local_date, override_provider)
            if direct_result is not None:
                matches, bootstrap_meta = direct_result
            else:
                matches, bootstrap_meta = await fetch_inventory_matches(runner)
            source_meta['primary_provider'] = str((bootstrap_meta or {}).get('provider') or getattr(settings, 'match_bootstrap_provider', '') or '')
            source_meta['requested_bootstrap_provider'] = override_provider or original_setting
            source_meta['attempts'] = dict((bootstrap_meta or {}).get('attempts') or {})
            source_meta['stats'] = dict((bootstrap_meta or {}).get('stats') or {})
            source_meta['preview'] = dict((bootstrap_meta or {}).get('preview') or {})
        except Exception as exc:
            direct_error = dict(source_meta)
            if override_provider and original_setting:
                restore_bootstrap_provider(settings, original_setting, original_env)
                runner = PredictionRunner(settings)
                matches, bootstrap_meta = await fetch_inventory_matches(runner)
                source_meta['primary_provider'] = str((bootstrap_meta or {}).get('provider') or getattr(settings, 'match_bootstrap_provider', '') or '')
                source_meta['requested_bootstrap_provider'] = override_provider
                source_meta['fallback_from'] = override_provider
                source_meta['fallback_reason'] = f'{type(exc).__name__}: {exc}'
                if direct_error:
                    source_meta['failed_direct_bootstrap'] = direct_error
                source_meta['attempts'] = dict((bootstrap_meta or {}).get('attempts') or {})
                source_meta['stats'] = dict((bootstrap_meta or {}).get('stats') or {})
                source_meta['preview'] = dict((bootstrap_meta or {}).get('preview') or {})
            else:
                raise

        matches_for_day = [
            match
            for match in matches
            if store.local_date_for_dt(match.commence_time) == local_date
        ]
        existing = store.load_inventory(local_date)
        payload = store.build_payload(
            local_date=local_date,
            matches=matches_for_day,
            source_meta=source_meta,
            existing=existing,
        )
        paths = store.save_inventory(payload)

        result = {
            'date_local': local_date,
            'build_status': 'ok',
            'bootstrap_provider': source_meta.get('primary_provider'),
            'requested_bootstrap_provider': source_meta.get('requested_bootstrap_provider'),
            'matches_total_raw': len(matches),
            'matches_for_day': len(matches_for_day),
            'saved_paths': paths,
            'counts': dict(payload.get('counts') or {}),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        summary_path = store.save_failure_summary(
            local_date=local_date,
            error_text=f'{type(exc).__name__}: {exc}',
            source_meta=source_meta,
            bootstrap_provider=str(source_meta.get('primary_provider') or getattr(settings, 'match_bootstrap_provider', '') or ''),
        )
        result = {
            'date_local': local_date,
            'build_status': 'error',
            'error': f'{type(exc).__name__}: {exc}',
            'summary_path': summary_path,
            'source_meta': source_meta,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    finally:
        restore_bootstrap_provider(settings, original_setting, original_env)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == '__main__':
    raise SystemExit(main())
