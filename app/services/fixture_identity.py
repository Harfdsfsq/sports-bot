"""Conservative team identity checks without acronym/substring fuzzy matches."""
from __future__ import annotations

import re
from datetime import timedelta
from difflib import SequenceMatcher

from app.utils import canonicalize_team_name, parse_datetime

_GENERIC = {'united', 'city', 'town', 'real', 'racing', 'sporting', 'athletic', 'club', 'sbv', 'us', 'calcio'}


def squad_identity(name):
    text = str(name or '').lower()
    youth = re.findall(r'\b(?:u|under)[ -]?(\d{2})\b', text)
    reserve = bool(re.search(r'\b(?:ii|iii|reserves?|резерв|дубль)\b|(?:\s|^)(?:b|2|3)$', text))
    women = bool(re.search(r'\b(?:women|woman|ladies|femenino|femenina|wfc|женщины|w)\b', text))
    return tuple(sorted(youth)), reserve, women


def same_team_identity(target, aliases):
    def normalized(name):
        text = str(name or '').translate(str.maketrans({'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd'}))
        return ' '.join(token for token in canonicalize_team_name(text).split() if token not in {'fodbold', 'football', 'futbol'})

    left = normalized(target)
    if not left:
        return False
    for alias in aliases:
        if squad_identity(target) != squad_identity(alias):
            continue
        right = normalized(alias)
        if not right:
            continue
        if left == right or {left, right} == {'agf aarhus', 'agf'}:
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
