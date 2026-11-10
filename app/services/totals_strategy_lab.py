"""Prospective, price-aware experiments. No bankroll, publication or live promotion.

Protocol parameters are fixed before outcomes. Every model sees identical quotes;
calibration uses the first frozen anchor per fixture, never only selected winners.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

from app.services.daily_match_registry import parse_time, match_identity
from app.services.daily_goal_probability import total_probability
from app.services.fixture_identity import same_team_identity
from app.services.market_period import is_full_match_market

PROTOCOL = 'priced-totals-v1'
MODELS = ('market', 'current_goals', 'opponent', 'shrunk', 'overdispersed', 'calibrated')


def logit(p):
    p = max(.001, min(.999, p))
    return math.log(p / (1-p))


def calibrated(raw, market, profile):
    z = logit(market) + profile['alpha']*(logit(raw)-logit(market)) + profile['intercept']
    return 1/(1+math.exp(-max(-20., min(20., z))))


def fit_calibration(rows, now):
    """Chronological fit/validation; outcomes must have been observed before now."""
    usable = []
    for row in rows:
        observed = parse_time(row.get('resolved_at'))
        created = parse_time(row.get('created_at'))
        kickoff = parse_time(row.get('kickoff'))
        if (row.get('protocol') != PROTOCOL or row.get('status') != 'resolved'
                or not observed or not created or not kickoff or not created < kickoff < observed < now):
            continue
        anchor = row['anchor']
        if row['total'] == anchor['point']:
            continue
        usable.append((kickoff, row['id'], anchor['shrunk'], anchor['market'], float(row['total'] > anchor['point'])))
    usable.sort()
    fallback = {'alpha': 0., 'intercept': 0., 'status': 'insufficient_history',
                'count': len(usable), 'trained_before': now.isoformat()}
    if len(usable) < 100:
        return fallback
    boundary = usable[int(len(usable)*.75)][0]
    train = [r for r in usable if r[0] < boundary]
    valid = [r for r in usable if r[0] >= boundary]
    if len(train) < 70 or len(valid) < 25:
        return fallback
    # Penalized logistic correction around the market; no library dependency.
    alpha, intercept = 0., 0.
    for _ in range(300):
        ga, gb = .04*alpha, .1*intercept
        for _, _, raw, market, y in train:
            p = calibrated(raw, market, {'alpha': alpha, 'intercept': intercept})
            ga += (p-y)*(logit(raw)-logit(market))/len(train)
            gb += (p-y)/len(train)
        alpha = max(0., min(1., alpha-.3*ga))
        intercept = max(-.5, min(.5, intercept-.3*gb))
    profile = {'alpha': alpha, 'intercept': intercept}
    def loss(use_model):
        return mean(-math.log(max(1e-9, p if y else 1-p))
                    for _, _, raw, market, y in valid
                    for p in [calibrated(raw, market, profile) if use_model else market])
    baseline, challenger = loss(False), loss(True)
    accepted = challenger < baseline
    return {**fallback, 'alpha': alpha if accepted else 0., 'intercept': intercept if accepted else 0.,
            'status': 'validated_correction' if accepted else 'market_fallback',
            'train_count': len(train), 'validation_count': len(valid), 'split_at': boundary.isoformat(),
            'validation_log_loss': challenger, 'market_log_loss': baseline,
            'training_hash': hashlib.sha256('|'.join(r[1] for r in usable).encode()).hexdigest()}


def paired_lines(offers, now, allowed_books=None):
    """Same bookmaker, exact full-time line; no synthetic cross-book vig removal."""
    groups = defaultdict(dict)
    for offer in offers:
        if offer.family != 'totals' or not is_full_match_market(offer):
            continue
        point, price = offer.point, offer.price
        if point is None or not math.isfinite(point) or not (point*2).is_integer() or not 1.5 <= point <= 5.5:
            continue
        if not math.isfinite(price) or price <= 1:
            continue
        at = parse_time(offer.metadata.get('fetched_at_utc'))
        if at is None or not -timedelta(seconds=5) <= now-at <= timedelta(minutes=15):
            continue
        book = offer.bookmaker.strip().lower()
        if allowed_books is not None and book not in allowed_books:
            continue
        side = str(offer.selection).lower()
        side = 'over' if side.startswith('over') else 'under' if side.startswith('under') else None
        if side is None:
            continue
        old = groups[(point, book)].get(side)
        # Conflicting quotes within a response use the conservative price.
        if old is None or price < old['price']:
            groups[(point, book)][side] = {'price': price, 'observed_at': at.isoformat(), 'source': offer.source}
    lines = defaultdict(list)
    for (point, book), quotes in sorted(groups.items()):
        if set(quotes) != {'over', 'under'}:
            continue
        a, b = quotes['over']['price'], quotes['under']['price']
        margin = 1/a+1/b
        if not 1 <= margin <= 1.2:
            continue
        lines[point].append({'book': book, 'quotes': quotes, 'market_over': (1/a)/margin})
    return [{'point': point, 'books': books, 'market': mean(b['market_over'] for b in books)}
            for point, books in sorted(lines.items()) if len(books) >= 2]


def distribution(rate, point, dispersion=0.):
    """Conditional over and push. NB2 is an explicitly separate challenger."""
    if dispersion <= 1e-6:
        d = total_probability(rate, point)
        return d.decisive_win, d.push
    size = 1/dispersion
    p = (size/(size+rate))**size
    below, push = 0., 0.
    for k in range(math.floor(point)+1):
        if k == point:
            push = p
        else:
            below += p
        p *= (k+size)/(k+1)*rate/(size+rate)
    over = max(0., 1-below-push)
    return over/(1-push), push


def evaluate_lines(lines, rates, profile):
    evaluated = []
    for line in lines:
        point, market = line['point'], line['market']
        predictions = {'market': (market, distribution(rates['shrunk'], point)[1])}
        for name in ('current_goals', 'opponent', 'shrunk', 'overdispersed'):
            predictions[name] = distribution(rates['shrunk'] if name == 'overdispersed' else rates[name], point,
                                             rates['dispersion'] if name == 'overdispersed' else 0.)
        predictions['calibrated'] = (calibrated(predictions['shrunk'][0], market, profile), predictions['shrunk'][1])
        evaluated.append({**line, 'predictions': {k: {'over': p, 'push': push} for k, (p, push) in predictions.items()}})
    picks = {}
    for name in MODELS:
        options = []
        for line in evaluated:
            p = line['predictions'][name]
            for side in ('over', 'under'):
                book = max(line['books'], key=lambda b: (b['quotes'][side]['price'], b['book']))
                odds = book['quotes'][side]['price']
                probability = p['over'] if side == 'over' else 1-p['over']
                ev = (probability*odds-1)*(1-p['push'])
                if 1.5 <= odds <= 3.2 and ev >= .03:
                    options.append({'point': line['point'], 'side': side, 'odds': odds, 'book': book['book'],
                                    'p': probability, 'push': p['push'], 'ev': ev,
                                    'quote_observed_at': book['quotes'][side]['observed_at']})
        picks[name] = max(options, key=lambda p: (p['ev'], -p['odds'], p['point'], p['side'])) if options else None
    # Fixed anchor, independent of model EV or eventual outcome, for paired scoring.
    anchor = min(evaluated, key=lambda line: (abs(line['point']-2.5), line['point']))
    return evaluated, picks, {'point': anchor['point'], **{name: p['over'] for name, p in anchor['predictions'].items()}}


def resolve(rows, history, now):
    """Quarantine conflicting or ambiguous identities, even after an earlier settlement."""
    results = defaultdict(list)
    for r in history.data['results'].values():
        results[int(parse_time(r['kickoff']).timestamp())//900].append(r)
    for row in rows:
        kickoff = parse_time(row['kickoff'])
        if now < kickoff+timedelta(hours=2):
            continue
        slot = int(kickoff.timestamp())//900
        possible = [r for offset in (-1, 0, 1) for r in results.get(slot+offset, [])
                    if abs(parse_time(r['kickoff'])-kickoff) <= timedelta(minutes=15)
                    and same_team_identity(row['home'], [r['home']]) and same_team_identity(row['away'], [r['away']])]
        reason = None
        if not possible:
            reason = 'result_not_found'
        elif any(r.get('conflict') for r in possible) or len({(r['hg'], r['ag']) for r in possible}) > 1:
            reason = 'conflicting_result'
        elif len(possible) > 1:
            # Multiple providers may store the same fixture under different
            # canonical aliases. Coalesce only exact-time, identified receipts;
            # matching totals alone cannot establish fixture identity.
            ids = defaultdict(set)
            for r in possible:
                for provider, identity in (r.get('provider_ids') or {}).items():
                    ids[provider].add(str(identity))
            if (any(parse_time(r['kickoff']) != kickoff for r in possible)
                    or any(not r.get('provider_ids') for r in possible)
                    or any(len(values) != 1 for values in ids.values())):
                reason = 'ambiguous_identity'
        observed_times = [parse_time(r.get('observed_at')) for r in possible]
        created = parse_time(row.get('created_at'))
        if reason is None and (not created or any(not observed or not created < observed <= now for observed in observed_times)):
            reason = 'result_observation_time_invalid'
        if reason:
            row.update(status='pending', resolution_reason=reason)
            for key in ('total', 'resolved_at', 'result_evidence'):
                row.pop(key, None)
            continue
        r = possible[0]
        row.update(status='resolved', total=r['hg']+r['ag'], resolved_at=max(observed_times).isoformat(),
                   result_evidence={'records': len(possible),
                                    'provider_ids': {provider: sorted({str(result['provider_ids'][provider])
                                        for result in possible if provider in (result.get('provider_ids') or {})})
                                        for provider in {name for result in possible for name in (result.get('provider_ids') or {})}}})
        row.pop('resolution_reason', None)


def summarize(rows):
    settled = [r for r in rows if r['status'] == 'resolved']
    metrics = {}
    for name in MODELS:
        pairs, pnl, days = [], [], set()
        score_groups, profit_groups = defaultdict(list), defaultdict(list)
        for row in settled:
            y = row['total'] > row['anchor']['point']
            if row['total'] != row['anchor']['point']:
                p, market = row['anchor'][name], row['anchor']['market']
                pairs.append(((p-y)**2, (market-y)**2, -math.log(max(1e-9, p if y else 1-p))))
                score_groups[row['kickoff'][:10]].append((p-y)**2-(market-y)**2)
            pick = row['picks'][name]
            if pick:
                difference = (row['total']-pick['point'])*(1 if pick['side']=='over' else -1)
                pnl.append(pick['odds']-1 if difference > 0 else -1 if difference < 0 else 0.)
                days.add(row['kickoff'][:10])
                profit_groups[row['kickoff'][:10]].append(pnl[-1])
        metrics[name] = {'paired_count': len(pairs), 'brier': mean(p[0] for p in pairs) if pairs else None,
                         'market_brier': mean(p[1] for p in pairs) if pairs else None,
                         'log_loss': mean(p[2] for p in pairs) if pairs else None,
                         'bets': len(pnl), 'paper_pnl': round(sum(pnl), 4),
                         'roi_pct': 100*mean(pnl) if pnl else None, 'days': len(days),
                         'roi_interval_95': cluster_interval(profit_groups),
                         'brier_difference_interval_95': cluster_interval(score_groups)}
    return {'protocol': PROTOCOL, 'tracked': len(rows), 'settled': len(settled),
            'pending': len(rows)-len(settled), 'models': metrics, 'automatic_live_promotion': False,
            'readiness': 'research_only', 'minimum_review': {'settled_picks': 200, 'distinct_days': 30},
            'promotion_requires': ['untouched_forward_period', 'paired_score_improvement',
                                  'positive_roi_with_uncertainty', 'executable_prices', 'manual_review']}


def cluster_interval(groups):
    """Exploratory day-block bootstrap, not an automatic significance test."""
    if len(groups) < 10:
        return None
    sums = [(sum(g), len(g)) for _, g in sorted(groups.items())]
    rng, values = random.Random(731), []
    for _ in range(1000):
        sample = [rng.choice(sums) for _ in sums]
        values.append(sum(s for s, _ in sample)/sum(n for _, n in sample))
    values.sort()
    return {'low': values[24], 'high': values[974], 'unit': 'fraction', 'blocks': len(groups),
            'method': 'day_block_bootstrap_1000', 'exploratory': True}


def run_lab(history, matches, contexts, registry, now, allowed_books=None):
    rows = history.data.setdefault('priced_totals_trials', [])
    resolve(rows, history, now)
    profile = fit_calibration(rows, now)
    new, skips = 0, Counter()
    for match in sorted(matches, key=lambda m: (m.commence_time, m.match_key)):
        if not timedelta(minutes=30) <= match.commence_time-now <= timedelta(hours=4):
            continue
        if any(abs(parse_time(r['kickoff'])-match.commence_time) <= timedelta(minutes=15)
               and same_team_identity(match.home_team, [r['home']]) and same_team_identity(match.away_team, [r['away']]) for r in rows):
            continue
        context = contexts.get(match.match_key)
        research = getattr(history, 'current_predictions', {}).get(match.match_key)
        if not context or not research:
            skips['missing_paired_model'] += 1
            continue
        evidence = context.details.get('daily_context_reliability', {})
        sample = evidence.get('best_team_sample')
        if sample is None or sample < 6 or context.expected_home is None or context.expected_away is None:
            skips['unverified_sample'] += 1
            continue
        offers = registry.cached('odds_api_io', 'offers', match) or []
        lines = paired_lines(offers, now, allowed_books)
        if not lines:
            skips['no_fresh_paired_prices'] += 1
            continue
        rates = {**research, 'current_goals': context.expected_home+context.expected_away}
        if not all(math.isfinite(rates[k]) and .1 <= rates[k] <= 15 for k in ('current_goals', 'opponent', 'shrunk')):
            skips['invalid_goal_rates'] += 1
            continue
        evaluated, picks, anchor = evaluate_lines(lines, rates, profile)
        rows.append({'id': match_identity(match), 'match_key': match.match_key, 'protocol': PROTOCOL, 'home': match.home_team,
                     'away': match.away_team, 'league': match.league_name, 'kickoff': match.commence_time.isoformat(),
                     'created_at': now.isoformat(), 'status': 'pending', 'rates': rates,
                     'evidence': evidence, 'provenance': context.details.get('daily_evidence_snapshot', []),
                     'lines': evaluated, 'picks': picks, 'anchor': anchor, 'calibration': profile})
        new += 1
    history.save()
    from app.services import totals_strategy_clv
    totals_strategy_clv.register(rows, now)
    return {**summarize(rows), 'new': new, 'skips': dict(skips), 'calibration': profile,
            'price_check': totals_strategy_clv.summary()}
