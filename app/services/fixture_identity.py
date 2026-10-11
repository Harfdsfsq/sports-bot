"""Conservative team identity checks without acronym/substring fuzzy matches."""
from __future__ import annotations

from datetime import timedelta
from difflib import SequenceMatcher

from app.utils import canonicalize_team_name, parse_datetime

_GENERIC = {'united', 'city', 'town', 'real', 'racing', 'sporting', 'athletic', 'club', 'sbv', 'us', 'calcio'}


def same_team_identity(target, aliases):
    left = canonicalize_team_name(target)
    if not left:
        return False
    for alias in aliases:
        right = canonicalize_team_name(alias)
        if not right:
            continue
        if left == right:
            return True
        a, b = set(left.split()), set(right.split())
        anchors = {t for t in a & b if len(t) >= 5 and t not in _GENERIC}
        if anchors and (a <= b or b <= a):
            return True
        # Long near-identical full names can tolerate a spelling variation.
        if min(len(left), len(right)) >= 8 and SequenceMatcher(None, left, right).ratio() >= .90:
            return True
    return False


def direct_fixture_identity(match, event, *, tolerance_hours=.5):
    """Both teams must match in their original home/away orientation."""
    home = event.get('home_aliases') or [event.get('home')]
    away = event.get('away_aliases') or [event.get('away')]
    if not same_team_identity(match.home_team, home) or not same_team_identity(match.away_team, away):
        return False
    try:
        kickoff = parse_datetime(event.get('commence_time'))
        return kickoff is not None and abs(match.commence_time - kickoff) <= timedelta(hours=tolerance_hours)
    except (TypeError, ValueError, AttributeError):
        return False


def football_data_context_matches(match, context):
    payload = context.payload or {}
    row = payload.get('scheduled', payload)
    home, away = row.get('homeTeam') or {}, row.get('awayTeam') or {}
    event = {
        'home_aliases': [home.get('name'), home.get('shortName')],
        'away_aliases': [away.get('name'), away.get('shortName')],
        'commence_time': row.get('utcDate'),
    }
    return direct_fixture_identity(match, event)
