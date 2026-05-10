from __future__ import annotations

"""Schema/runtime patch for RapidApiOddsBridgeProvider.

The OddsFeed RapidAPI endpoint can return generic historical multi-sport rows
when called with the old broad `/api/v1/events?sport=football` query. Those rows
use nested fields like `team_home.name`, `team_away.name`, `tournament.name` and
`start_at`, which the generic bridge did not parse. Result: requests=1,
events_fetched=100, events_matched=0, offers_parsed=0.

This patch is conservative:
- tries date/soccer-specific OddsFeed paths before generic paths;
- parses nested team/league/start fields;
- rejects clearly non-soccer or stale rows before fuzzy matching;
- leaves all publication guards unchanged.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.utils import parse_datetime, score_event_match

UTC = timezone.utc
_INSTALLED = False


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get('name') or value.get('Name') or value.get('title') or value.get('slug')
    return str(value or '').strip()


def _nested(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ''):
            return value
    return None


def _sport_name(row: dict[str, Any]) -> str:
    sport = row.get('sport')
    if isinstance(sport, dict):
        return str(sport.get('slug') or sport.get('name') or sport.get('id') or '').lower()
    return str(row.get('sport_slug') or row.get('sportName') or row.get('sport') or row.get('sport_id') or '').lower()


def _is_soccer_row(row: dict[str, Any]) -> bool:
    sport = _sport_name(row)
    if not sport:
        return True
    return any(token in sport for token in ('football', 'soccer', '1')) and not any(token in sport for token in ('american', 'aussie', 'tennis', 'basket', 'hockey'))


def _event_start_patched(row: dict[str, Any]) -> datetime | None:
    for key in (
        'start_at', 'startAt', 'start_time', 'startTime', 'startsAt',
        'commence_time', 'commenceTime', 'date', 'eventDate', 'event_date',
        'kickoff', 'time', 'timestamp', 'startTimestamp', 'start_timestamp',
    ):
        value = row.get(key)
        if value in (None, ''):
            continue
        try:
            if str(key).lower().endswith('timestamp') or key == 'timestamp':
                raw = float(value)
                if raw > 100000000000:
                    raw = raw / 1000.0
                return datetime.fromtimestamp(raw, tz=UTC)
            dt = parse_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            continue
    return None


def _pick_nested_team(row: dict[str, Any], side: str) -> str:
    keys = (
        ('team_home', 'home_team', 'homeTeam', 'home', 'team1', 'participant1Name', 'home_name', 'homeName')
        if side == 'home'
        else ('team_away', 'away_team', 'awayTeam', 'away', 'team2', 'participant2Name', 'away_name', 'awayName')
    )
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            text = _text(value)
        else:
            text = _text(value)
        if text:
            return text
    return ''


def _pick_nested_league(row: dict[str, Any]) -> str:
    for key in ('league', 'leagueName', 'competition', 'competitionName', 'tournament', 'tournamentName', 'category'):
        text = _text(row.get(key))
        if text:
            return text
    return ''


def _match_event_patched(self: Any, row: dict[str, Any], matches: list[Any]):
    if not isinstance(row, dict) or not _is_soccer_row(row):
        return None
    home = _pick_nested_team(row, 'home')
    away = _pick_nested_team(row, 'away')
    league = _pick_nested_league(row)
    start = _event_start_patched(row)
    if not home or not away or start is None:
        return None
    now = datetime.now(UTC)
    # Secondary odds rescue is for current/near-window fixtures, not historical feed rows.
    max_future_hours = float(os.getenv('RAPIDAPI_ODDS_EVENT_MAX_FUTURE_HOURS', '48') or 48)
    max_past_hours = float(os.getenv('RAPIDAPI_ODDS_EVENT_MAX_PAST_HOURS', '3') or 3)
    delta_hours = (start - now).total_seconds() / 3600.0
    if delta_hours < -max_past_hours or delta_hours > max_future_hours:
        return None
    best = None
    for match in matches:
        score, quality = score_event_match(
            sport='soccer',
            match_home=getattr(match, 'home_team', ''),
            match_away=getattr(match, 'away_team', ''),
            match_start=getattr(match, 'commence_time', start),
            match_league=getattr(match, 'league_name', ''),
            event_home=home,
            event_away=away,
            event_start=start,
            event_league=league,
            exact_tolerance_hours=float(getattr(self.settings, 'match_start_tolerance_hours', 12) or 12),
            fuzzy_tolerance_hours=max(float(getattr(self.settings, 'fallback_match_start_tolerance_hours', 8) or 8), 18.0),
        )
        if best is None or score > best[2]:
            best = (match, quality, score)
    if best is None or best[2] < (70.0 if best[1] == 'fuzzy' else 52.0):
        return None
    return best[0], row, best[1]


def _build_providers_patched(original):
    def wrapper(self):
        providers = original(self)
        today = datetime.now(UTC).date()
        tomorrow = today + timedelta(days=1)
        for provider in providers:
            if provider.get('key') != 'odds_feed':
                continue
            provider['event_paths'] = [
                f'/api/v1/events?sport_id=1&status=NOT_STARTED&date={today.isoformat()}',
                f'/api/v1/events?sport=football&status=NOT_STARTED&date={today.isoformat()}',
                f'/api/v1/events?sport=soccer&status=NOT_STARTED&date={today.isoformat()}',
                f'/api/v1/events?sport_id=1&from={today.isoformat()}&to={tomorrow.isoformat()}',
                f'/api/v1/events?sport=football&from={today.isoformat()}&to={tomorrow.isoformat()}',
                f'/api/v1/events?sport=soccer&from={today.isoformat()}&to={tomorrow.isoformat()}',
                f'/api/v1/events?sport_id=1&start_at_from={today.isoformat()}&start_at_to={tomorrow.isoformat()}',
                f'/api/v1/events?sport=football&start_at_from={today.isoformat()}&start_at_to={tomorrow.isoformat()}',
            ]
            provider['schema_patch'] = 'oddsfeed-date-soccer-v1'
        return providers
    return wrapper


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    try:
        from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    if getattr(RapidApiOddsBridgeProvider, '_harizon_schema_patch_installed', False):
        _INSTALLED = True
        return {'status': 'already_patched'}
    RapidApiOddsBridgeProvider._event_start = staticmethod(_event_start_patched)
    RapidApiOddsBridgeProvider._match_event = _match_event_patched
    RapidApiOddsBridgeProvider._build_providers = _build_providers_patched(RapidApiOddsBridgeProvider._build_providers)
    RapidApiOddsBridgeProvider._harizon_schema_patch_installed = True
    _INSTALLED = True
    return {'status': 'installed', 'patch': 'oddsfeed-date-soccer-v1'}
