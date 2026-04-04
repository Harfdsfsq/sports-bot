from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from statistics import median

TEAM_ALIAS_MAP = {
    'red bull bragantino': 'bragantino',
    'rb bragantino': 'bragantino',
    'athletico paranaense': 'athletico pr',
    'atletico paranaense': 'athletico pr',
    'vasco gama': 'vasco da gama',
    'internacional rs': 'internacional',
}

STOP_WORDS = {
    'fc', 'cf', 'ac', 'sc', 'club', 'fk', 'bk', 'hc', 'bc', 'kk', 'de', 'da', 'do', 'the'
}

LOW_TIER_PATTERNS = [
    r'\bu19\b',
    r'\bu20\b',
    r'\bu21\b',
    r'\byouth\b',
    r'\breserve\b',
    r"\bwomen'?s\b",
    r'\besports\b',
    r'\bfriendly\b',
]


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r'[^a-z0-9\s]+', ' ', value)
    tokens = [t for t in value.split() if t and t not in STOP_WORDS]
    value = ' '.join(tokens)
    return TEAM_ALIAS_MAP.get(value, value)


def dedupe_key(sport_key: str, home_team: str, away_team: str, date_iso: str) -> str:
    return f'{sport_key}|{normalize_text(home_team)}|{normalize_text(away_team)}|{date_iso}'


def build_match_key(sport_key: str, home_team: str, away_team: str, commence_time: datetime) -> str:
    dt = ensure_utc(commence_time)
    return dedupe_key(sport_key, home_team, away_team, dt.date().isoformat())


def build_loose_match_key(sport_key: str, home_team: str, away_team: str) -> str:
    return f'{sport_key}|{normalize_text(home_team)}|{normalize_text(away_team)}'


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_low_tier_league(league_name: str) -> bool:
    value = league_name.lower()
    return any(re.search(pattern, value) for pattern in LOW_TIER_PATTERNS)


def implied_probability(decimal_odds: float) -> float:
    return 1.0 / decimal_odds if decimal_odds > 0 else 0.0


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def weighted_median(values: list[tuple[float, float]]) -> float:
    if not values:
        raise ValueError('weighted_median requires at least one value')
    expanded: list[float] = []
    for value, weight in values:
        expanded.extend([value] * max(1, int(round(weight))))
    return float(median(expanded))


def poisson_over_probability(expected_total: float, line: float) -> float:
    threshold = math.floor(line)
    push_on_half = abs(line - threshold - 0.5) < 1e-9
    less_or_equal = sum(
        math.exp(-expected_total) * (expected_total ** goals) / math.factorial(goals)
        for goals in range(0, threshold + (0 if push_on_half else 1))
    )
    return clamp(1.0 - less_or_equal, 0.01, 0.99)


def shrink_probability(model_prob: float, market_prob: float, confidence: float) -> float:
    shrink = clamp(0.18 + (confidence / 100.0) * 0.32, 0.18, 0.50)
    return market_prob + (model_prob - market_prob) * shrink


def russian_market_name(family: str) -> str:
    mapping = {
        'h2h': 'Исход',
        'totals': 'Тотал',
        'spreads': 'Фора',
        'dnb': 'Фора 0',
        'doubleChance': 'Двойной шанс',
        'btts': 'Обе забьют',
        'teamTotals': 'Инд. тотал',
    }
    return mapping.get(family, family)


def russian_selection(family: str, selection: str, point: float | None = None) -> str:
    low = selection.strip().lower()
    if family == 'totals':
        if low.startswith('over'):
            return 'Больше'
        if low.startswith('under'):
            return 'Меньше'
    if family == 'h2h':
        if low == 'draw':
            return 'Ничья'
    if family == 'btts':
        if low in {'yes', 'both teams to score - yes'}:
            return 'Да'
        if low in {'no', 'both teams to score - no'}:
            return 'Нет'
    return selection
