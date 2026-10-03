"""Poisson score probabilities for supported daily markets, including refunds."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementProbability:
    win: float
    push: float
    loss: float

    @property
    def decisive_win(self):
        return self.win / (self.win + self.loss)


def _weights(mean):
    mean = float(mean)
    if not math.isfinite(mean) or not 0 <= mean <= 30:
        raise ValueError('Invalid football goal intensity')
    bound = math.ceil(mean + 12 * math.sqrt(mean) + 20)
    rows = [math.exp(-mean)]
    for goals in range(1, bound + 1):
        rows.append(rows[-1] * mean / goals)
    return rows


def _line(point):
    point = float(point)
    if not math.isfinite(point) or not (point * 2).is_integer():
        raise ValueError('Only integer and half-goal lines are supported')
    return point


def _settlement(rows):
    win, push, loss = rows
    total = win + push + loss
    if total <= 0 or win + loss <= 0:
        raise ValueError('No decisive outcome probability')
    return SettlementProbability(win / total, push / total, loss / total)


def total_probability(mean, point):
    """Over probability; under uses loss, with equality treated as a refund."""
    point = _line(point)
    rows = [0., 0., 0.]
    for goals, probability in enumerate(_weights(mean)):
        rows[0 if goals > point else 1 if goals == point else 2] += probability
    return _settlement(rows)


def spread_probability(home, away, point, side):
    """Selected team's goal difference plus its signed handicap must exceed zero."""
    point = _line(point)
    if side not in {'home', 'away'}:
        raise ValueError('Missing handicap team side')
    rows = [0., 0., 0.]
    for h, hp in enumerate(_weights(home)):
        for a, ap in enumerate(_weights(away)):
            margin = (h - a if side == 'home' else a - h) + point
            rows[0 if margin > 0 else 1 if margin == 0 else 2] += hp * ap
    return _settlement(rows)


def push_probability(family, home, away, point, side):
    if point is None or not float(point).is_integer():
        return 0.
    if family == 'totals':
        return total_probability(home + away, point).push
    if family == 'teamTotals':
        return total_probability(home if side == 'home' else away, point).push
    if family == 'spreads':
        return spread_probability(home, away, point, side).push
    return 0.


def expected_value(probability, odds, push=0.):
    """Conditional win estimate; refunds return the stake and have zero profit."""
    return (probability * odds - 1) * (1 - push) * 100
