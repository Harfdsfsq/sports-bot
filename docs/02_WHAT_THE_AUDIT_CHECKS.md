# What the odds audit checks

The audit flags a candidate when one or more conditions hold:

- `abs((1 / odds) - implied_probability)` is too large;
- `odds / fair_odds` is too large;
- `odds` differs from `source_summary.selected_price`.

This is especially useful for totals and side markets where line normalization or offer selection may have produced an inconsistent candidate.

Outputs:
- `artifacts/odds-integrity-report.json`
- `artifacts/odds-traces/*.json`
