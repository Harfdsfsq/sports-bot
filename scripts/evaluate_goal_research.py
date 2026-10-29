"""Offline walk-forward score evaluation. No API calls or Telegram transport."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta

from app.services.daily_match_registry import parse_time, write_json
from app.services.goal_research import GoalHistory, fit_model, predict, over25, score_forecasts, VERSION


def evaluate(history, limit=300):
    candidates = sorted((r for r in history.data['results'].values() if not r.get('conflict')), key=lambda r: r['kickoff'])[-limit:]
    forecasts, skipped = [], Counter()
    for row in candidates:
        cutoff = parse_time(row['kickoff']) - timedelta(hours=4)
        training = history.eligible(row['league'], cutoff)
        model = fit_model(training, cutoff)
        values = predict(model, row['home'], row['away'])
        if values is None:
            skipped['insufficient_history'] += 1
            continue
        forecasts.append({'kickoff': row['kickoff'], 'p_over25': over25(sum(values)),
                          'baseline_over25': over25(model['league_total']), 'total': row['hg'] + row['ag']})
    return {'version': VERSION, 'mode': 'historical_walk_forward', 'evaluated': len(forecasts),
            'skips': dict(skipped), **score_forecasts(forecasts),
            'limitations': ['Current corrected historical results are used; original publication vintages may be unavailable.',
                            'No historic executable odds: these scores do not establish betting profitability.']}


def evaluate_comparison(history, limit=300):
    from app.services.goal_comparison import variants, scores, PROTOCOL
    candidates = sorted((r for r in history.data['results'].values() if not r.get('conflict')), key=lambda r:r['kickoff'])[-limit:]
    rows, cache = [], {}
    for row in candidates:
        # One fit per league/date, strictly before that entire day's fixtures.
        day = parse_time(row['kickoff']).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = day-timedelta(hours=4)
        key = (row['league'], day)
        if key not in cache:
            cache[key] = fit_model(history.eligible(row['league'], cutoff, prospective=True), cutoff)
        model = cache[key]
        predictions = variants(model, row['home'], row['away']) if model else None
        if predictions:
            rows.append({'kickoff':row['kickoff'], 'total':row['hg']+row['ag'],
                         'comparison':{'protocol':PROTOCOL,'models':predictions}})
    times = sorted({row['kickoff'] for row in rows})
    if len(times)<2:
        return {'status':'insufficient_history','evaluated':len(rows)}
    boundary = times[max(1,int(len(times)*.6))]
    validation = [r for r in rows if r['kickoff']<boundary]
    test = [r for r in rows if r['kickoff']>=boundary]
    names = ('league','opponent_poisson','shrunk_poisson')
    selection = {name:scores(validation,name) for name in names}
    winner = min(names,key=lambda name:selection[name]['brier'])
    return {'protocol':PROTOCOL,'mode':'historical_comparison','validation':selection,
            'test':{name:scores(test,name) for name in names},'selected_on_validation':winner,
            'split_at':boundary,'evaluated':len(rows),'automatic_live_promotion':False,
            'limitations':['Corrected historical data; original observation vintages may be unavailable.',
                           'Model selection uses validation only. Test results must not be used for retuning.',
                           'No executable historical odds: this does not establish profitability.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', default='.data/daily_quality/goal-history.json')
    parser.add_argument('--compare', action='store_true')
    parser.add_argument('--limit', type=int, default=300)
    parser.add_argument('--output', default='.data/exports/latest-goal-walk-forward.json')
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000')
    result = (evaluate_comparison if args.compare else evaluate)(GoalHistory(args.history), args.limit)
    write_json(args.output, result)
    print(result)


if __name__ == '__main__':
    main()
