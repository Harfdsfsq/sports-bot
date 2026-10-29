"""Frozen, paired goal-model experiments. No live promotion or market fitting."""
from __future__ import annotations

import math
from statistics import mean

PROTOCOL = 'goal-comparison-v1'


def variants(model, home, away):
    from app.services.goal_research import predict, over25, resolve_team
    rates = predict(model, home, away)
    if rates is None:
        return None
    league = model['league_total']
    prior = (model.get('league_home', league/2), model.get('league_away', league/2))
    n = min(model['counts'][resolve_team(model, home)], model['counts'][resolve_team(model, away)])
    weight = n/(n+20.)  # Predeclared regularization; not selected on paper outcomes.
    shrunk = tuple(weight*a+(1-weight)*b for a,b in zip(rates, prior))
    return {'league': {'p': over25(sum(prior)), 'expected_total': sum(prior)},
            'opponent_poisson': {'p': over25(sum(rates)), 'expected_total': sum(rates)},
            'shrunk_poisson': {'p': over25(sum(shrunk)), 'expected_total': sum(shrunk)}}


def scores(rows, name):
    pairs = [(r['comparison']['models'][name]['p'], float(r['total']>2.5),
              r['comparison']['models'][name]['expected_total'], r['total']) for r in rows]
    if not pairs:
        return {'count': 0}
    bins = []
    for low in range(0, 100, 10):
        bucket = [(p,y) for p,y,_,_ in pairs if low/100 <= p < (low+10)/100]
        if bucket:
            bins.append({'from_pct': low, 'count': len(bucket), 'mean_probability': mean(p for p,y in bucket),
                         'observed_frequency': mean(y for p,y in bucket)})
    return {'count': len(pairs), 'brier': mean((p-y)**2 for p,y,_,_ in pairs),
            'log_loss': mean(-math.log(max(1e-9,p if y else 1-p)) for p,y,_,_ in pairs),
            'goal_bias_actual_minus_expected': mean(y-x for _,_,x,y in pairs), 'calibration_bins': bins}


def summarize(forecasts):
    eligible = [f for f in forecasts if (f.get('comparison') or {}).get('protocol') == PROTOCOL]
    settled = [f for f in eligible if f.get('status')=='resolved']
    # All models are evaluated on exactly the same frozen fixtures.
    return {'protocol': PROTOCOL, 'mode': 'paper_only', 'tracked': len(eligible), 'settled': len(settled),
            'pending': len(eligible)-len(settled), 'automatic_live_promotion': False,
            'models': {name: scores(settled,name) for name in ('league','opponent_poisson','shrunk_poisson')}}
