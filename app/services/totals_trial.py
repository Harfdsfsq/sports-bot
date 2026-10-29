"""Prospective overall-total trial, isolated from bankroll and live calibration."""
from __future__ import annotations

import math
from collections import Counter
from statistics import mean

from app.services.daily_match_registry import parse_time
from app.services.prediction_audit import invalid_prediction_reasons

PROTOCOL = 'overall-totals-forward-v1'


def summarize(rows, version):
    eligible, excluded = [], Counter()
    for row in rows:
        if row.get('validation_protocol') != PROTOCOL or row.get('goal_model_version') != version:
            continue
        at, kickoff = parse_time(row.get('published_at')), parse_time(row.get('commence_time_utc'))
        if invalid_prediction_reasons(row):
            excluded['data_incident'] += 1
        elif row.get('family') != 'totals' or not row.get('reserve_only') or row.get('telegram_sent'):
            excluded['not_paper_total'] += 1
        elif not at or not kickoff or (kickoff - at).total_seconds() < 1800:
            excluded['not_frozen_before_kickoff'] += 1
        else:
            eligible.append(row)
    # A protocol selects one line before kickoff. Never choose by eventual profit.
    from app.services.daily_quality_runner import same_reserve_fixture
    unique = []
    for row in sorted(eligible, key=lambda r: r['published_at']):
        if any(same_reserve_fixture(row, previous) for previous in unique):
            excluded['duplicate_fixture'] += 1
        else:
            unique.append(row)
    settled = [r for r in unique if r.get('status') in {'won', 'lost', 'push', 'void'}]
    pnl = [float(r['odds']) - 1 if r['status'] == 'won' else -1. if r['status'] == 'lost' else 0. for r in settled]
    pairs = []
    for row in settled:
        if row['status'] not in {'won', 'lost'}:
            continue
        try:
            p, market = float(row['adjusted_probability_pct']) / 100, float(row['market_probability_pct']) / 100
            if not all(math.isfinite(v) and 0 < v < 1 for v in (p, market)):
                continue
            y = float(row['status'] == 'won')
            pairs.append(((p-y)**2, (market-y)**2))
        except (KeyError, TypeError, ValueError):
            continue
    outcomes = Counter(r.get('status') for r in unique)
    segments = {}
    for side in ('over', 'under'):
        subset = [r for r in settled if r.get('selection_key') == side]
        segment_pnl = sum(float(r['odds'])-1 if r['status']=='won' else -1 if r['status']=='lost' else 0 for r in subset)
        segments[side] = {'settled':len(subset), 'wins':sum(r['status']=='won' for r in subset),
                          'losses':sum(r['status']=='lost' for r in subset), 'paper_pnl':round(segment_pnl,3)}
        goals = []
        for r in subset:
            try:
                actual = float(r['final_home_goals'])+float(r['final_away_goals'])
                expected = float(r['expected_home'])+float(r['expected_away'])
                if math.isfinite(actual) and math.isfinite(expected):
                    goals.append(actual-expected)
            except (KeyError, TypeError, ValueError):
                continue
        segments[side].update(goal_pairs=len(goals), goal_bias_actual_minus_expected=mean(goals) if goals else None)
    return {'protocol': PROTOCOL, 'goal_model_version': version, 'mode': 'paper_only',
            'tracked': len(unique), 'settled': len(settled), 'pending': outcomes['shadow_pending'],
            'wins': outcomes['won'], 'losses': outcomes['lost'], 'pushes': outcomes['push'], 'voids': outcomes['void'],
            'paper_pnl': round(sum(pnl), 3),
            'paper_roi_pct': round(mean(pnl) * 100, 3) if pnl else None,
            'paired_scores': len(pairs),
            'brier': mean(a for a, _ in pairs) if pairs else None,
            'market_brier': mean(b for _, b in pairs) if pairs else None,
            'segments': segments, 'excluded': dict(excluded), 'automatic_live_promotion': False}
