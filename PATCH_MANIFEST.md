# sports-bot run263045 v8 window counter fix

## Problem
`send_harizon_telegram_run_report_v8.py` had a direct v8 `main()`, but `_current_inventory_window_payload()` incremented `window_0_4h_strict_ready`, `window_0_4h_already_published`, `window_0_12h_strict_ready` and `window_0_12h_already_published` without initializing those keys. When coverage-truth contained a match in the current window, v8 crashed and the workflow fell back to v7.

## Fix
- Initialize all strict/already-sent window counters before iteration.
- Make the Coverage truth block distinguish fresh publish-ready, strict-ready and already-sent strict-ready matches.

## Files
- `scripts/send_harizon_telegram_run_report_v8.py`
- `tests/test_report_v8_window_counter_init.py`
