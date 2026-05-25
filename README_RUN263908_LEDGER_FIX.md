# Run 263908/263900 accumulation ledger fix

This patch fixes the accumulation stage after v9 reports started working.

## Fixed

- `scripts/update_prediction_ledger.py`
  - restores both `norm` and `_norm` naming compatibility;
  - reads nested `metrics` from `latest-controlled-fallback-report.json`;
  - writes `latest-prediction-ledger-summary.json` on every successful run;
  - stores EV, edge, quality, odds, books/context/odds-source counts for rejected/watch-only candidates.

- `scripts/build_prediction_calibration_audit.py`
  - reads nested fallback/API/quality metrics;
  - fills `home_team`, `away_team`, `odds`, `ev_after_pct`, `edge_after_pp`, and `quality`.

## Expected next run

`Build prediction analysis artifacts` should no longer fail with `NameError: norm is not defined`.
`prediction-ledger.jsonl` and `latest-prediction-ledger-summary.json` should be uploaded with populated metrics.
