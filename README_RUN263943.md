# run263943 accumulation analytics v5

Fixes the remaining accumulation-stage duplication where sparse candidate-value rows
without `point` were stored separately from the rich fallback/API row with the
same match/family/selection and concrete line.

Expected effect on run 263943:
- `latest-prediction-calibration-audit.json` candidate_keys: 2 -> 1
- `line_less_rows_collapsed`: >= 1
- `prediction-ledger.jsonl` current run rows: 2 -> 1 for Paderborn/Wolfsburg
- `rows_missing_core_metrics_current_run`: remains 0

No publication guards are changed.
