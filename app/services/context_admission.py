"""Evidence admission for the daily full-match football model."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.fixture_identity import direct_fixture_identity, fixture_squad_reasons, football_data_context_matches, same_team_identity
from app.utils import parse_datetime


def identity_reasons(provider, match, context):
    squad_reasons = fixture_squad_reasons(match)
    if squad_reasons:
        return squad_reasons
    payload = context.payload or {}
    if provider == 'football_data':
        return [] if football_data_context_matches(match, context) else ['context_fixture_identity_mismatch']
    if provider == 'thesportsdb':
        for side, team in [('home', match.home_team), ('away', match.away_team)]:
            row = payload.get(side + '_row') or {}
            if not same_team_identity(team, [row.get('strTeam') or row.get('name')]):
                return ['context_team_identity_mismatch']
        if context.details.get('thesportsdb_partial_context'):
            return ['partial_sporting_context']
    if provider == 'sstats' and context.source == 'sstats_form':
        for side, team in [('home', match.home_team), ('away', match.away_team)]:
            rows = payload.get(side + '_recent') or []
            if not rows or any(not same_team_identity(team, [row.get('team') or row.get('team_key')]) for row in rows):
                return ['context_team_identity_mismatch']
            for row in rows:
                try:
                    if parse_datetime(row.get('start')) >= match.commence_time:
                        return ['context_future_history']
                except (TypeError, ValueError, AttributeError):
                    return ['context_history_time_missing']
    if provider == 'sstats' and context.source != 'sstats_form':
        from app.providers.sstats import SStatsContextProvider
        home = SStatsContextProvider._extract_team_name(payload, 'home')
        away = SStatsContextProvider._extract_team_name(payload, 'away')
        if not same_team_identity(match.home_team, [home]) or not same_team_identity(match.away_team, [away]):
            return ['context_team_identity_mismatch']
    if provider == 'bzzoiro':
        event = payload.get('event') or (payload.get('prediction') or {}).get('event') or {}
        if event and not direct_fixture_identity(match, {'home': event.get('home_team'), 'away': event.get('away_team'), 'commence_time': event.get('event_date')}):
            return ['context_fixture_identity_mismatch']
    if provider == 'espn':
        event = payload.get('event') or {}
        competition = (event.get('competitions') or [{}])[0]
        teams = {c.get('homeAway'): c.get('team') or {} for c in competition.get('competitors') or []}
        event_identity = {
            'home_aliases': [teams.get('home', {}).get(k) for k in ('displayName', 'shortDisplayName', 'name')],
            'away_aliases': [teams.get('away', {}).get(k) for k in ('displayName', 'shortDisplayName', 'name')],
            'commence_time': event.get('date') or competition.get('date'),
        }
        if not direct_fixture_identity(match, event_identity):
            return ['context_fixture_identity_mismatch']
    if provider == 'api_football':
        teams = payload.get('teams') or {}
        if any(not same_team_identity(team, [(teams.get(side) or {}).get('name')]) for side, team in [('home', match.home_team), ('away', match.away_team)]):
            return ['context_team_identity_mismatch']
    if provider == 'openligadb':
        checked = False
        table = payload.get('table') or {}
        history = payload.get('history') or {}
        for side, team in [('home', match.home_team), ('away', match.away_team)]:
            row = table.get(side + '_row')
            if row:
                checked = True
                if not same_team_identity(team, [row.get('teamName') or row.get('TeamName')]):
                    return ['context_team_identity_mismatch']
            for row in history.get('recent_' + side) or []:
                checked = True
                names = [(row.get('team1') or {}).get('teamName'), (row.get('team2') or {}).get('teamName')]
                if not same_team_identity(team, names):
                    return ['context_team_identity_mismatch']
        if not checked:
            return ['context_identity_missing']
    return []


def source_is_fresh(provider, context, observed_at, now):
    try:
        observed = parse_datetime(observed_at)
        if observed is None or not -timedelta(seconds=5) <= now - observed <= timedelta(hours=6):
            return False
        if provider == 'bzzoiro':
            created = context.details.get('bzzoiro_prediction_created_at')
            if created and not -timedelta(seconds=5) <= now - parse_datetime(created) <= timedelta(hours=24):
                return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def admit_context(provider, match, context, observed_at, now=None):
    now = now or datetime.now(UTC)
    reasons = identity_reasons(provider, match, context)
    if not source_is_fresh(provider, context, observed_at, now):
        reasons.append('context_source_stale')
    return reasons
