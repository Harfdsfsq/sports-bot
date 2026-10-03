"""Explicit publication policy for the daily inventory pipeline."""
from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any


def enabled() -> bool:
    return os.getenv('PUBLICATION_PROFILE', '').strip().lower() == 'daily_quality'


def quality_decision(candidate: Any, coverage: dict, *, now: datetime) -> tuple[str, list[str], dict]:
    summary = candidate.source_summary
    q = float(summary.get('quality_score') or 0)
    p = float(candidate.adjusted_probability)
    odds = float(candidate.odds)
    edge = (p - 1 / odds) * 100 if odds > 1 else -100
    ev = (p * odds - 1) * 100
    reasons = []
    # Quality scores describe ranking, never an independently verified win rate.
    lead = (candidate.commence_time.astimezone(UTC) - now).total_seconds() / 60
    if not 30 <= lead <= 240:
        reasons.append('kickoff_outside_30m_4h')
    if not all(math.isfinite(v) for v in (q, p, odds, edge, ev, float(candidate.confidence))) or not 0 < p < 1 or not 1.5 <= odds <= 3.2:
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
    if str(summary.get('context_source') or '').lower() in {'market', 'market_signal', 'market_implied_xg', 'model', 'unknown'}:
        reasons.append('synthetic_context')
    point = getattr(candidate, 'point', None)
    if point is not None and (not math.isfinite(float(point)) or not float(point * 2).is_integer()):
        reasons.append('unsupported_quarter_line')
    if candidate.family == 'totals' and (candidate.expected_home is None or candidate.expected_away is None):
        reasons.append('missing_goal_model')
    if ev < 3 or edge < 2 or q < 65 or float(candidate.confidence) < 60:
        reasons.append('below_b_quality')
    tier = 'A' if q >= 78 and float(candidate.confidence) >= 70 and ev >= 5 and edge >= 3 else 'B'
    return tier, reasons, {
        'publication_tier': tier if not reasons else 'blocked',
        'quality_score': q, 'canonical_ev_pct': round(ev, 3), 'canonical_edge_pp': round(edge, 3),
        'tier_definition': 'quality_and_value',
        'extra_confirmation_bonus': int(coverage.get('odds_sources_count') or 0) > 1 or int(coverage.get('context_sources_count') or 0) > 1,
        'tier_limits': {'A': {'quality': 78, 'confidence': 70, 'ev': 5, 'edge': 3}, 'B': {'quality': 65, 'confidence': 60, 'ev': 3, 'edge': 2}},
    }
