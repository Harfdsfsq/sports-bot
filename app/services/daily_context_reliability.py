"""Provisional evidence policy, independent of the candidate's claimed value.

Counts are per team, not the number of APIs. Thresholds require prospective
validation; neither this score nor provider confidence is a win probability.
"""
from __future__ import annotations

import math
import os
from dataclasses import replace


def a_sample_floor():
    return max(3, int(os.getenv('DAILY_A_MIN_TEAM_SAMPLE', '6')))


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _count(value):
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def sample_support(context):
    """Only use sample fields belonging to this provider and its actual mode."""
    d = context.details or {}
    source = str(d.get('daily_evidence_provider') or context.source).lower()
    pairs = {
        'sstats': [('home_recent_count', 'away_recent_count')],
        'sstats_form': [('home_recent_count', 'away_recent_count')],
        'football_data': [('football_data_home_sample', 'football_data_away_sample'),
                          ('football_data_home_played', 'football_data_away_played')],
        'openligadb': [('openligadb_history_home_sample', 'openligadb_history_away_sample'),
                       ('openligadb_table_home_matches', 'openligadb_table_away_matches')],
        'thesportsdb': [('thesportsdb_home_played', 'thesportsdb_away_played')],
    }
    home = away = None
    for h, a in pairs.get(source, []):
        home, away = _count(d.get(h)), _count(d.get(a))
        if home is not None and away is not None:
            break
    if home is None or away is None:
        # Actual saved histories are also usable; forecasts/form percentages
        # without an explicit count never become fabricated match samples.
        payload = context.payload or {}
        if source in {'football_data', 'openligadb', 'sstats', 'sstats_form'}:
            h, a = payload.get('recent_home'), payload.get('recent_away')
            if isinstance(h, list) and isinstance(a, list):
                home, away = len(h), len(a)
    if source == 'thesportsdb' and (home is None or away is None):
        payload = context.payload or {}
        def played(row):
            for key in ('intPlayed', 'played', 'gamesPlayed', 'games'):
                value = _count(row.get(key))
                if value is not None:
                    return value
            return None
        home, away = played(payload.get('home_row') or {}), played(payload.get('away_row') or {})
    minimum = min(home, away) if home is not None and away is not None else None
    status = 'unknown' if minimum is None else 'thin' if minimum < 3 else 'limited' if minimum < a_sample_floor() else 'supported'
    if minimum is None and source == 'football_data' and d.get('football_data_mode') == 'standings':
        status = 'unverified_table'
    return {'provider': source, 'home': home, 'away': away, 'minimum': minimum, 'status': status}


def blend_contexts(contexts, weight_for_source):
    """Flat blend: no repeated source-count bonus or order-dependent averaging."""
    if not contexts:
        raise ValueError('No sporting contexts to blend')
    contexts = list(contexts)
    supports = [sample_support(c) for c in contexts]
    # Clamp invalid provider projections before they enter any ensemble.
    cleaned = []
    for c in contexts:
        def valid(field, low, high, current=c):
            value = _number(getattr(current, field))
            return value if value is not None and low <= value <= high else None
        cleaned.append(replace(c, expected_home=valid('expected_home', .15, 4.8),
                               expected_away=valid('expected_away', .15, 4.8),
                               home_win_probability=valid('home_win_probability', 0, 1),
                               away_win_probability=valid('away_win_probability', 0, 1),
                               confidence=max(0., min(100., _number(c.confidence) or 0.))))
    contexts = cleaned
    weights = []
    for c, support in zip(contexts, supports, strict=True):
        n = support['minimum']
        reliability = 0. if support['status'] == 'unverified_table' else .5 if n is None else n / (n + 6.)
        weights.append(max(0., float(c.confidence)) * max(0., weight_for_source(c)) * reliability)
    if sum(weights) <= 0:
        weights = [1.] * len(contexts)

    def average(field):
        pairs = []
        for c, weight in zip(contexts, weights, strict=True):
            value = getattr(c, field)
            if value is not None and math.isfinite(float(value)) and weight > 0:
                pairs.append((float(value), weight))
        return sum(v * w for v, w in pairs) / sum(w for _, w in pairs) if pairs else None

    home, away = average('expected_home'), average('expected_away')
    predictive = [(c, s, w) for c, s, w in zip(contexts, supports, weights, strict=True)
                  if c.expected_home is not None and c.expected_away is not None]
    predictive_weight = sum(w for _, _, w in predictive)
    supported_weight = sum(w for _, s, w in predictive if s['status'] == 'supported')
    share = supported_weight / predictive_weight if predictive_weight else 0.
    disagreement = 0.
    if predictive_weight and home is not None and away is not None:
        disagreement = math.sqrt(sum(w * ((float(c.expected_home) - home) ** 2 + (float(c.expected_away) - away) ** 2) / 2
                                     for c, _, w in predictive) / predictive_weight)
    known = [s['minimum'] for _, s, _ in predictive if s['minimum'] is not None]
    best_sample = max(known) if known else None
    all_thin = bool(predictive) and all(s['status'] in {'thin', 'unverified_table'} for _, s, _ in predictive)
    a_supported = share >= .5 and disagreement <= float(os.getenv('DAILY_A_MAX_GOAL_DISAGREEMENT', '.65'))
    cap = 59. if all_thin else 69. if not a_supported else 100.
    details, profits = {}, {}
    for c in contexts:
        details.update(c.details or {})
        profits.update(c.profits or {})
    evidence = {'version': 'evidence-v1', 'sources': supports, 'best_team_sample': best_sample,
                'supported_weight_share': round(share, 4), 'goal_disagreement': round(disagreement, 4),
                'a_supported': a_supported, 'all_thin': all_thin, 'confidence_cap': cap}
    details.update(daily_context_reliability=evidence, merged_sources=[c.source for c in contexts])
    return replace(contexts[0], source=contexts[0].source if len(contexts) == 1 else 'ensemble',
                   payload=contexts[0].payload if len(contexts) == 1 else {'sources': {s['provider']: c.payload for c, s in zip(contexts, supports, strict=True)}},
                   expected_home=home, expected_away=away,
                   home_win_probability=average('home_win_probability'), away_win_probability=average('away_win_probability'),
                   confidence=min(cap, average('confidence')), details=details, profits=profits)


def evidence_quality(candidate):
    s = candidate.source_summary
    evidence = dict(s.get('daily_context_reliability') or {})
    n = evidence.get('best_team_sample')
    score = 70. if n is None else 60. + min(20., float(n) * 20. / 12.)
    score += min(6., max(0, int(candidate.books_count) - 1) * 3.)
    score += min(3., max(0, int(candidate.sources_count) - 1) * 1.5)
    score += min(2., max(0, int(s.get('context_sources_count') or 1) - 1))
    if candidate.books_count <= 1:
        score -= 2.
    score -= min(12., float(evidence.get('goal_disagreement') or 0) * 12.)
    if evidence.get('all_thin'):
        score = min(score, 60.)
    elif not evidence.get('a_supported'):
        score = min(score, 76.)
    return max(0., min(100., score))
