# run263731 report-v9 pool-filter fix

Files:
- `scripts/send_harizon_telegram_run_report_v9.py`
- `tests/test_report_v9_pool_filter_classifier.py`
- `run263731-report-v9-pool-filter.patch`

Apply:
1. Copy the new script and test into the repository.
2. Apply `run263731-report-v9-pool-filter.patch` or manually put v9 before v8 in the HARIZON report workflow step.

Purpose:
- Do not treat source-pool counters like `debug_candidates_before_quality: 4` as pre-evaluation fallback filters.
- Keep real filters such as `*_not_in_day_inventory`, `*_stale_or_outside_window`, and `*_prefilter`.

Publication logic is unchanged.
