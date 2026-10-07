"""Explicit publication policy for the daily inventory pipeline."""
from __future__ import annotations

import math
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.daily_goal_probability import expected_value

TIER_LIMITS = {
    'A': {'quality': 78, 'confidence': 70, 'ev': 5, 'edge': 3},
    'B': {'quality': 65, 'confidence': 60, 'ev': 3, 'edge': 2},
}


def enabled() -> bool:
    return os.getenv('PUBLICATION_PROFILE', '').strip().lower() == 'daily_quality'


def quality_decision(candidate: Any, coverage: dict, *, now: datetime) -> tuple[str, list[str], dict]:
    summary = candidate.source_summary
    q = float(summary.get('quality_score') or 0)
    p = float(candidate.adjusted_probability)
    odds = float(candidate.odds)
    edge = (p - 1 / odds) * 100 if odds > 1 else -100
    push = float(summary.get('model_push_probability') or 0)
    ev = expected_value(p, odds, push)
    reasons = []
    # Quality scores describe ranking, never an independently verified win rate.
    lead = (candidate.commence_time.astimezone(UTC) - now).total_seconds() / 60
    if not 30 <= lead <= 240:
        reasons.append('kickoff_outside_30m_4h')
    if not all(math.isfinite(v) for v in (q, p, odds, edge, ev, push, float(candidate.confidence))) or not 0 <= push < 1 or not 0 < p < 1 or not 1.5 <= odds <= 3.2:
        reasons.append('invalid_probability_or_odds')
    if min(int(coverage.get('odds_sources_count') or 0), int(coverage.get('context_sources_count') or 0), int(coverage.get('books_count') or 0)) < 1:
        reasons.append('missing_real_line_or_context')
    for key, minutes in [('daily_price_observed_at', 15), ('daily_context_observed_at', 360)]:
        try:
            observed = datetime.fromisoformat(str(summary[key]).replace('Z', '+00:00'))
            if observed.tzinfo is None or not -timedelta(seconds=5) <= now - observed.astimezone(UTC) <= timedelta(minutes=minutes):
                reasons.append(key + '_stale')
        except (ValueError, KeyError, TypeError):
            reasons.append(key + '_missing')
    if str(summary.get('context_source') or '').lower() in {'market', 'market_signal', 'market_implied_xg', 'model', 'unknown', 'weather'}:
        reasons.append('synthetic_context')
    allowed = {v.strip().lower() for v in os.getenv('PUBLICATION_ALLOWED_MARKET_FAMILIES', 'totals,spreads,teamTotals').split(',')}
    if candidate.family.lower() not in allowed:
        reasons.append('market_outside_daily_policy')
    point = getattr(candidate, 'point', None)
    if point is not None and (not math.isfinite(float(point)) or not float(point * 2).is_integer()):
        reasons.append('unsupported_quarter_line')
    if candidate.family == 'totals' and (candidate.expected_home is None or candidate.expected_away is None):
        reasons.append('missing_goal_model')
    floor = TIER_LIMITS['B']
    if ev < floor['ev'] or edge < floor['edge'] or q < floor['quality'] or float(candidate.confidence) < floor['confidence']:
        reasons.append('below_b_quality')
    top = TIER_LIMITS['A']
    tier = 'A' if q >= top['quality'] and float(candidate.confidence) >= top['confidence'] and ev >= top['ev'] and edge >= top['edge'] else 'B'
    return tier, reasons, {
        'publication_tier': tier if not reasons else 'blocked',
        'quality_score': q, 'canonical_ev_pct': round(ev, 3), 'canonical_edge_pp': round(edge, 3),
        'tier_definition': 'quality_and_value',
        'extra_confirmation_bonus': int(coverage.get('odds_sources_count') or 0) > 1 or int(coverage.get('context_sources_count') or 0) > 1,
        'tier_limits': TIER_LIMITS,
    }


def sporting_context_sources(values):
    """Normalize actual sporting API names; aliases and weather are not extra APIs."""
    providers = ('sstats', 'bzzoiro', 'football_data', 'thesportsdb', 'espn', 'openligadb')
    result = set()
    for value in values:
        for name in re.split(r'[,;+|:/]', str(value).lower()):
            name = name.strip()
            if name in {'bzzoiro_event_odds', 'market_implied_xg', 'market_signal'}:
                continue
            for provider in providers:
                if name == provider or name.startswith(provider + '_'):
                    result.add(provider)
    return result
