"""Conservative team identity checks without acronym/substring fuzzy matches."""
from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime

_GENERIC = {'united', 'city', 'town', 'real', 'racing', 'sporting', 'athletic', 'club', 'sbv', 'us', 'calcio'}
_REVIEWED_GROUPS = (frozenset({'js djidjel', 'js jijel'}), frozenset({'rc arbaa', 'rc arba'}))
_TEAM_TRANSLATION = str.maketrans({'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd'})
_NORMALIZED_ALIASES = {
    'js djidjel': 'js jijel', 'rc arbaa': 'rc arba',
    'san martin s j': 'san martin san juan', 'san martin sj': 'san martin san juan',
}


def squad_identity(name):
    return _squad_identity(str(name or ''))


@lru_cache(maxsize=16384)
def _squad_identity(name):
    text = name.lower()
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


class _TeamName(NamedTuple):
    canonical: str
    canonical_tokens: frozenset[str]
    normalized: str
    tokens: frozenset[str]
    anchors: frozenset[str]
    squad: tuple[tuple[str, ...], bool, bool]
    characters: Counter[str]


_prepared_names: ContextVar[dict[str, _TeamName] | None] = ContextVar("prepared_team_names", default=None)


@contextmanager
def team_name_cache():
    """Reuse normalized names within one synchronous pass."""
    if _prepared_names.get() is not None:
        yield
        return
    token = _prepared_names.set({})
    try:
        yield
    finally:
        _prepared_names.reset(token)


def _team_name(name: str) -> _TeamName:
    cache = _prepared_names.get()
    if cache is not None and name in cache:
        return cache[name]
    canonical = canonicalize_team_name(name)
    translated = name.translate(_TEAM_TRANSLATION)
    normalized_name = canonical if translated == name else canonicalize_team_name(translated)
    key = ' '.join(token for token in normalized_name.split()
                   if token not in {'fodbold', 'football', 'futbol'})
    normalized = _NORMALIZED_ALIASES.get(key, key)
    tokens = frozenset(normalized.split())
    anchors = frozenset(token for token in tokens if len(token) >= 5 and token not in _GENERIC)
    prepared = _TeamName(canonical, frozenset(canonical.split()), normalized, tokens, anchors,
                         squad_identity(name), Counter(normalized))
    if cache is not None:
        cache[name] = prepared
    return prepared


def _reviewed(first: _TeamName, second: _TeamName) -> bool:
    a, b = first.canonical, second.canonical
    return first.squad == second.squad and a != b and any(a in group and b in group for group in _REVIEWED_GROUPS)


def reviewed_team_alias_match(first, second):
    return _reviewed(_team_name(str(first or '')), _team_name(str(second or '')))


def _same_team(first: _TeamName, second: _TeamName) -> bool:
    left, right = first.normalized, second.normalized
    if first.squad != second.squad or not left or not right:
        return False
    if left == right or {left, right} == {'agf aarhus', 'agf'}:
        return True
    if first.anchors & second.anchors and (first.tokens <= second.tokens or second.tokens <= first.tokens):
        return True
    shortest = min(len(left), len(right))
    length = len(left) + len(right)
    if shortest < 8 or 2 * shortest / length < .90:
        return False
    # Shared characters bound the number of matching characters from above.
    # Only the original SequenceMatcher ratio can accept a fuzzy match.
    overlap = sum(min(count, second.characters.get(char, 0)) for char, count in first.characters.items())
    return 2 * overlap / length >= .90 and SequenceMatcher(None, left, right).ratio() >= .90


def same_team_identity(target, aliases):
    first = _team_name(str(target or ''))
    return any(_same_team(first, _team_name(str(alias or ''))) for alias in aliases)


def same_history_team_identity(target, aliases):
    """Keep reviewed abbreviations and spelling variants without city-only matches."""
    first = _team_name(str(target or ''))
    for alias in aliases:
        second = _team_name(str(alias or ''))
        # A strict canonical subset is rejected unless explicitly reviewed.
        if _reviewed(first, second) or {first.canonical, second.canonical} == {'agf', 'agf aarhus'}:
            if _same_team(first, second):
                return True
            continue
        a, b = first.canonical_tokens, second.canonical_tokens
        if a and b and not (a < b or b < a) and _same_team(first, second):
            return True
    return False


def history_team_matches(match, side, row):
    """Revalidate cached form against the fixture's witnessed provider team ID."""
    from app.services.sstats_targeted_history import verified_fixture

    target = getattr(match, side + '_team')
    fixture = verified_fixture(match)
    witnessed_name = row.get('team') or row.get('team_key')
    if fixture:
        expected = fixture[side + 'Team']
        if not same_history_team_identity(expected.get('name'), [witnessed_name]):
            return False
        sid = expected.get('id')
        if sid is not None and str(row.get('team_id') or '') != str(sid):
            return False
        country = (expected.get('country') or {}).get('code')
        return not (country and row.get('team_country') and country != row['team_country'])
    return same_history_team_identity(target, [witnessed_name])


def history_team_name(match, side):
    """Use the provider's name only when its future fixture receipt was verified."""
    from app.services.sstats_targeted_history import verified_fixture

    fixture = verified_fixture(match)
    return fixture[side + 'Team']['name'] if fixture else getattr(match, side + '_team')


def direct_fixture_identity(match, event, *, tolerance_hours=.5):
    """Both teams must match in their original home/away orientation."""
    home = event.get('home_aliases') or [event.get('home')]
    away = event.get('away_aliases') or [event.get('away')]
    if not same_team_identity(match.home_team, home) or not same_team_identity(match.away_team, away):
        from app.services.reviewed_fixture_aliases import scoped_fixture_alias_match

        return scoped_fixture_alias_match(match, event)
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
