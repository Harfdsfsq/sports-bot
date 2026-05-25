# run263959 accumulation analytics v7

Fixes the last analytics-only issue from run-bot-26395985264:

- `prediction-ledger.jsonl` still included current-run `api_coverage`-only rows.
- Those rows are useful for `latest-candidate-opportunity-audit.json`, but they are not real forecast candidates.
- They can miss `edge_pp` and should not count as missing-core forecast rows.

Add `scripts/repair_prediction_accumulation_outputs.py` after `update_prediction_ledger.py` in `run-bot.yml`.

Expected after next run:

```text
latest-prediction-ledger-summary.json:
rows_missing_core_metrics_current_run: 0
repair_removed_current_run_coverage_only_rows: >= 0
```

This patch is analytics-only. It does not change publication, EV, xG, quality, odds-source, or bankroll logic.
