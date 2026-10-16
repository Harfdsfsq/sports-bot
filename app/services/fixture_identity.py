"""Conservative team identity checks without acronym/substring fuzzy matches."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime

_GENERIC = {'united', 'city', 'town', 'real', 'racing', 'sporting', 'athletic', 'club', 'sbv', 'us', 'calcio'}


def squad_identity(name):
    text = str(name or '').lower()
    youth = re.findall(r'\b(?:u|under)[ -]?(\d{2})\b', text)
    reserve = bool(re.search(r'\b(?:ii|iii|reserves?|резерв|дубль)\b|(?:\s|^)(?:b|2|3)$', text))
    # A club initial (H W Welders / H&W Welders) is not a women's marker.
    # The short W convention is accepted only as a trailing squad suffix.
    women = bool(re.search(r'\b(?:women|woman|ladies|femenino|femenina|wfc|женщины)\b', text)
                 or re.search(r'(?:\s|\()w\)?$', text.strip()))
    return tuple(sorted(youth)), reserve, women


def team_identity_key(name):
    """Keep existing first-team keys, distinguish youth, women and reserves."""
    youth, reserve, women = squad_identity(name)
    suffix = [f'u{age}' for age in youth]
    if reserve:
        text = str(name or '').lower()
        level = '3' if re.search(r'\biii\b|(?:\s|^)3$', text) else '2'
        suffix.append('reserve' + level)
    if women:
        suffix.append('women')
    # The common normalizer handles U21 but not every spelling of Under 21.
    base_name = re.sub(r'\b(?:u|under)[ -]?\d{2}\b', ' ', str(name or ''), flags=re.I)
    base = canonicalize_team_name(base_name)
    return base + ('#' + ','.join(suffix) if suffix else '')


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


@lru_cache(maxsize=1)
def competition_squad_rules():
    path = Path(__file__).resolve().parents[2] / 'config/competition_squad_rules.json'
    return json.loads(path.read_text())['rules']


def fixture_squad_reasons(match):
    """A known reserve participant cannot silently become its first team.

    Rules are reviewed for a specified competition and season. No team names,
    fixture IDs or dates are guessed or rewritten from the roster.
    """
    league = canonicalize_league_name(getattr(match, 'league_name', '') or '')
    kickoff = getattr(match, 'commence_time', None)
    if not league or kickoff is None:
        return []
    for rule in competition_squad_rules():
        if kickoff.year != rule['season_year'] or league not in {canonicalize_league_name(v) for v in rule['league_names']}:
            continue
        for team in (getattr(match, 'home_team', ''), getattr(match, 'away_team', '')):
            base = re.sub(r'\b(?:(?:u|under)[ -]?\d{2}|ii|iii|reserves?|women|ladies)\b|(?:\s|^)(?:b|2|3)$', ' ', team, flags=re.I)
            # Full reviewed club names only: a city anchor such as Tallinn
            # must not make independent FC Tallinn become Levadia or Flora.
            tokens = set(canonicalize_team_name(base).split())
            if not any(tokens == set(canonicalize_team_name(alias).split())
                       for aliases in rule['club_aliases'] for alias in aliases):
                continue
            youth, reserve, women = squad_identity(team)
            expected_age = str(rule['required_squad']).removeprefix('u')
            if (youth == (expected_age,) or (not youth and reserve)) and not women:
                continue
            return ['fixture_squad_conflict' if youth or reserve or women else 'fixture_squad_ambiguous']
    return []
