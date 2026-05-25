# run263896 runtime-preflight + accumulation fix

## Problem seen in run-bot-26389644785

The Telegram report was v9, but the production `run-once` did not complete.
`latest-run-bot.log` contains:

`AttributeError: 'RuntimePreflight' object has no attribute 'apply_phase_policy'`

Because `run-once` crashed before the full CandidateFactory/fallback cycle:
- only cached/bootstrap evidence was available;
- controlled fallback report was not produced;
- prediction ledger stayed empty;
- v9 could not explain the real root cause and showed stale/cached coverage.

## Fix

- Adds `RuntimePreflight.apply_phase_policy()` and maps it to the normal pre-run preparation path.
- Makes v9 detect runtime tracebacks in `latest-run-bot.log` and clearly render a runtime error block.
- Makes `update_prediction_ledger.py` add a `runtime_error` row when a run crashes before candidates are available.
- Adds regression tests.

Publication guards are unchanged.
