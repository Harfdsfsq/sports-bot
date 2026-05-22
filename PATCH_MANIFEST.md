# sports-bot run262957 inventory clock + line movement patch

## Problem fixed

The run artifacts showed `minutes_to_kickoff` calculated from a stale timestamp (`2026-04-26T22:32:01Z`) instead of the current run time. For example, `Sumqayit FK — Qarabag FK` kicked off at `2026-05-22T15:30:00Z`, but the inventory stored `37017.97` minutes to kickoff. That broke the final pre-kickoff window, line movement lifecycle, and reporting.

## Files changed

- `.github/workflows/run-bot.yml`
  - Adds an explicit step before `Publish controlled fallback` to run:
    - `scripts/update_day_inventory_priority_and_line_state.py`
    - `scripts/repair_inventory_refresh_plan_counts.py`
  - Copies the updated inventory/line-movement artifacts into `artifacts/run-bot`.

- `scripts/update_day_inventory_priority_and_line_state.py`
  - `now_utc_from_debug()` no longer blindly trusts stale `.logs/debug-last-run.json`.
  - Supports `HARIZON_RUN_NOW_UTC`, `RUN_NOW_UTC`, `CURRENT_TIME_UTC` for deterministic runs/tests.
  - Falls back to wall-clock time if debug time is older than `MAX_TRUSTED_ARTIFACT_NOW_AGE_MINUTES`.

- `scripts/repair_inventory_refresh_plan_counts.py`
  - Same stale-debug timestamp protection.

- `tests/test_inventory_runtime_clock_and_workflow.py`
  - Regression tests for stale debug time, near-kickoff minutes, and workflow ordering.

## Validation

```bash
python -m py_compile scripts/update_day_inventory_priority_and_line_state.py scripts/repair_inventory_refresh_plan_counts.py scripts/publish_controlled_fallback.py scripts/send_harizon_telegram_run_report_v8.py
PYTHONPATH=. python -m pytest tests/test_inventory_runtime_clock_and_workflow.py tests/test_fallback_pool_post_quality_value_prefilter.py tests/test_report_active_core_renderer_merge.py -q
# 5 passed
```

## Artifact replay check

Using `HARIZON_RUN_NOW_UTC=2026-05-22T15:11:00+00:00` against the uploaded artifact, the patched inventory recalculates:

- `Sumqayit FK — Qarabag FK`: `minutes_to_kickoff = 19.0`, `pre_kickoff_status = too_soon`
- Final pre-kickoff checks in inventory state become non-zero instead of being hidden by the stale April timestamp.
