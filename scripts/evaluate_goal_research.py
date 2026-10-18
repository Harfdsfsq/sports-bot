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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', default='.data/daily_quality/goal-history.json')
    parser.add_argument('--limit', type=int, default=300)
    parser.add_argument('--output', default='.data/exports/latest-goal-walk-forward.json')
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000')
    result = evaluate(GoalHistory(args.history), args.limit)
    write_json(args.output, result)
    print(result)


if __name__ == '__main__':
    main()
