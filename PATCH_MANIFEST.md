# sports-bot run263092 fallback pool report fix

## Problem
The run correctly did not publish a pick, but the v8 report classified the situation as `quality/value не пропустили` even though controlled fallback evaluated zero candidates. The raw candidate was removed before evaluation by the day-inventory membership/lifecycle filter:

- `latest_rescue_candidates_not_in_day_inventory: 1`
- `debug_candidates_before_quality_not_in_day_inventory: 1`
- `fallback candidates_seen: 0`
- `fallback evaluated: 0`

This made Telegram misleading: it looked like the candidate failed quality/value, while in fact it was outside the frozen day-inventory contract.

## Changes
- `scripts/send_harizon_telegram_run_report_v8.py`
  - loads `latest-controlled-fallback-report.json`;
  - exposes `controlled_fallback_pool_counts` and `controlled_fallback_pool_filter_counts` in diagnostics;
  - changes v8 status to `candidates_filtered_before_fallback` when raw candidates exist but fallback evaluated zero due pre-evaluation filters;
  - renders a `Controlled fallback pool filter` block;
  - translates `*_not_in_day_inventory` into Russian in the main reason line.

- `tests/test_report_v8_fallback_pool_filter_status.py`
  - regression coverage for the new status and rendered block.

## Expected result
For this run, the report should say:

`Итог: 🟡 кандидаты отфильтрованы до fallback`

`Главная причина: кандидат не входит в frozen day inventory`

This does not relax publication rules. It only fixes report truthfulness.
