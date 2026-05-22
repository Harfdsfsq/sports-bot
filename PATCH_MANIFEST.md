# sports-bot run263110 runtime import side-effects fix

## Problem confirmed from `run-bot-26311029908.zip`

The bot correctly did not publish a forecast, but the zero-candidate diagnostics were degraded:

- `matches_with_offers=130`, `matches_with_context=129`, `raw_candidates=0`.
- CandidateFactory blockers were visible (`bucket_sources_below_2`, `market_derived_signal_guard_*`).
- `latest-post-integrity-candidate-rescue.json` contained only `{"status":"already_installed"}` instead of the actual build-time stage.

This happened because `app/services/__init__.py` installed runtime CandidateFactory patches as an import side effect. Report/fallback helper processes import service modules while reading artifacts, so they can overwrite real production diagnostics after the run.

## Files changed

- `app/services/__init__.py`
  - Now side-effect free.
  - Runtime patch installation remains the responsibility of `app.services.runtime_startup_chain` during the main production run.

- `app/services/post_integrity_candidate_rescue.py`
  - `install()` no longer overwrites `latest-post-integrity-candidate-rescue.json` when the patch is already installed.
  - This preserves the real `rescued`, `no_candidate`, or `pass_through` stage for the v8 report.

- `tests/test_runtime_import_side_effects.py`
  - Regression tests for import-side-effect safety and artifact preservation.

## Validation

```bash
python -m py_compile app/services/__init__.py app/services/post_integrity_candidate_rescue.py
PYTHONPATH=. python -m pytest -q tests/test_runtime_import_side_effects.py tests/test_report_v8_fallback_pool_filter_status.py tests/test_report_v8_window_counter_init.py tests/test_controlled_fallback_day_inventory_membership.py
# 8 passed
```

## Expected next-run effect

If raw candidates are still zero, Telegram v8 should preserve the real post-integrity rescue stage instead of showing a misleading `already_installed` marker. Publication guards are not weakened.
