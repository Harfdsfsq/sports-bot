from __future__ import annotations

import os
import sys
from datetime import timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.schemas import Match
from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime, score_event_match

UTC = timezone.utc
_INSTALLED = False
_PLACEHOLDER_SOURCES = {'', 'day_inventory', 'inventory', 'unknown', 'none', 'null'}


def _is_day_inventory_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else '').replace('\\', '/')
    return argv0.endswith('scripts/build_day_inventory.py') or argv0.endswith('build_day_inventory.py')


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _clean_source(value: Any) -> str:
    source = str(value or '').strip()
    if source.lower() in _PLACEHOLDER_SOURCES:
        return ''
    return source


def _row_source_ids(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (row.get('source_ids'), (row.get('metadata') or {}).get('provider_source_ids') if isinstance(row.get('metadata'), dict) else None):
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            source = _clean_source(key)
            source_id = str(value or '').strip()
            if source and source_id:
                out[source] = source_id
    return out


def _sources_seen(row: dict[str, Any], source_ids: dict[str, str]) -> list[str]:
    seen: set[str] = set(source_ids.keys())
    raw_seen = row.get('sources_seen')
    values = raw_seen if isinstance(raw_seen, list) else str(raw_seen or '').split(',')
    for item in values:
        source = _clean_source(item)
        if source:
            seen.add(source)
    return sorted(seen)


def _preferred_source(source_ids: dict[str, str], row: dict[str, Any]) -> tuple[str, str]:
    for source in ('odds_api_io', 'bzzoiro', 'sstats', 'football_data', 'thesportsdb', 'allsportsapi', 'sportlogic'):
        if source_ids.get(source):
            return source, str(source_ids[source])
    source = _clean_source(row.get('source'))
    source_id = str(row.get('source_event_id') or '').strip()
    if source and source_id:
        return source, source_id
    if source_ids:
        source = sorted(source_ids.keys())[0]
        return source, str(source_ids[source])
    return 'day_inventory', str(row.get('canonical_match_id') or row.get('match_key') or '')


def _inventory_match_from_row(self: Any, row: dict[str, Any]) -> Match | None:
    try:
        kickoff_raw = row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local')
        home = str(row.get('home_team') or '').strip()
        away = str(row.get('away_team') or '').strip()
        league = str(row.get('league_name') or '').strip()
        if not kickoff_raw or not home or not away or not league:
            return None
        source_ids = _row_source_ids(row)
        source, source_event_id = _preferred_source(source_ids, row)
        metadata = dict(row.get('metadata') or {}) if isinstance(row.get('metadata'), dict) else {}
        metadata['from_day_inventory'] = True
        metadata['source_ids'] = dict(source_ids)
        metadata['provider_source_ids'] = dict(source_ids)
        metadata['sources_seen'] = ','.join(_sources_seen(row, source_ids))
        if source_ids.get('odds_api_io'):
            metadata['odds_api_io_id'] = str(source_ids['odds_api_io'])
        metadata['inventory_top_score'] = row.get('inventory_top_score')
        metadata['inventory_selection_bucket'] = row.get('inventory_selection_bucket')
        return Match(
            source=source,
            source_event_id=source_event_id,
            sport_key=str(row.get('sport_key') or 'soccer'),
            league_name=league,
            home_team=home,
            away_team=away,
            commence_time=parse_datetime(str(kickoff_raw)),
            home_team_norm=str(row.get('home_team_norm') or canonicalize_team_name(home)),
            away_team_norm=str(row.get('away_team_norm') or canonicalize_team_name(away)),
            league_key=str(row.get('league_key') or canonicalize_league_name(league)),
            tier=str(row.get('tier') or 'mid'),
            metadata=metadata,
        )
    except Exception as exc:
        try:
            self.provider_runtime_errors['day_inventory'].append(self._format_exception(exc))
        except Exception:
            pass
        return None


def _ids_for_match(match: Match) -> set[str]:
    ids: set[str] = set()
    if str(getattr(match, 'source', '') or '').strip().lower() == 'odds_api_io':
        ids.add(str(getattr(match, 'source_event_id', '') or '').strip())
    meta = getattr(match, 'metadata', {}) or {}
    if isinstance(meta, dict):
        for key in ('odds_api_io_id', 'odds_api_io_event_id'):
            value = str(meta.get(key) or '').strip()
            if value:
                ids.add(value)
        for raw in (meta.get('source_ids'), meta.get('provider_source_ids')):
            if isinstance(raw, dict):
                value = str(raw.get('odds_api_io') or raw.get('odds-api.io') or '').strip()
                if value:
                    ids.add(value)
        raw_event = meta.get('raw_event')
        if isinstance(raw_event, dict):
            value = str(raw_event.get('id') or '').strip()
            if value:
                ids.add(value)
    return {item for item in ids if item and item.lower() not in _PLACEHOLDER_SOURCES}


def _patched_match_event(original):
    def wrapped(self: Any, event: dict[str, Any], matches: list[Match]) -> Match | None:
        event_id = str(event.get('id') or '').strip()
        if event_id:
            for match in matches:
                if event_id in _ids_for_match(match):
                    event['match_score'] = 120.0
                    event['match_quality'] = 'source_id'
                    return match
        matched = original(self, event, matches)
        if matched is not None:
            return matched
        if not _truthy(os.getenv('ODDS_API_IO_RELAXED_INVENTORY_MATCHING_ENABLED'), True):
            return None
        best_match: Match | None = None
        best_score = 0.0
        best_quality: str | None = None
        try:
            exact_tol = float(os.getenv('ODDS_API_IO_RELAXED_EXACT_TOLERANCE_HOURS') or getattr(self.settings, 'match_start_tolerance_hours', 12) or 12)
            fuzzy_tol = float(os.getenv('ODDS_API_IO_RELAXED_FUZZY_TOLERANCE_HOURS') or getattr(self.settings, 'fallback_match_start_tolerance_hours', 12) or 12)
            min_score = float(os.getenv('ODDS_API_IO_RELAXED_MIN_SCORE') or '42')
            for match in matches:
                score, quality = score_event_match(
                    sport=match.sport_key,
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=event.get('home'),
                    event_away=event.get('away'),
                    event_start=event.get('commence_time'),
                    event_league=event.get('league'),
                    exact_tolerance_hours=exact_tol,
                    fuzzy_tolerance_hours=fuzzy_tol,
                )
                if score > best_score:
                    best_score = score
                    best_quality = quality
                    best_match = match
            if best_match is not None and best_score >= min_score:
                event['match_score'] = best_score
                event['match_quality'] = f'relaxed_{best_quality or "fuzzy"}'
                return best_match
        except Exception:
            return None
        return None
    return wrapped


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if _is_day_inventory_process():
        return {'status': 'skipped_day_inventory_process'}
    try:
        from app.services.runner import PredictionRunner
        from app.providers.odds_api_io import OddsApiIoProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    PredictionRunner._match_from_day_inventory_row = _inventory_match_from_row
    original_match_event = getattr(OddsApiIoProvider, '_match_event', None)
    if callable(original_match_event) and not getattr(OddsApiIoProvider, '_harizon_inventory_id_match_patch', False):
        OddsApiIoProvider._match_event = _patched_match_event(original_match_event)
        OddsApiIoProvider._harizon_inventory_id_match_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'patches': ['PredictionRunner._match_from_day_inventory_row', 'OddsApiIoProvider._match_event']}
