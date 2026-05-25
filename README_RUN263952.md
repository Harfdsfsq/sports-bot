# run263952 accumulation analytics v6

This patch fixes the last accumulation-stage issue seen in `run-bot-26395206458`:
API coverage-only rows were being appended to `prediction-ledger.jsonl` as if they were forecast candidates.

Those rows are useful in coverage/opportunity audit, but they are not predictions until they reach value/fallback/quality/publication diagnostics. The new postprocess removes only current-run API-only rows from the prediction ledger and calibration audit, then rewrites the ledger summary.

Expected next run:

- `latest-prediction-accumulation-repair.json` exists.
- `latest-prediction-ledger-summary.json.rows_missing_core_metrics_current_run == 0` when fallback/value rows have core metrics.
- API-only rows remain visible through `latest-candidate-opportunity-audit.json` rather than polluting the prediction ledger.

Publication guards are unchanged.
