# sports-bot fast depth v2 fix

## Why

The fast workflow was launched correctly, but the production app received
`RUN_MODE=fast`. That triggered internal shortcuts before the budget settings could
help: the run processed only 36 matches, made only 4 odds-api.io odds requests,
produced 0 matches with 2+ bookmakers, and created no usable Bzzoiro secondary
odds. This made fast mode fast but not publish-capable.

## Changes

- `.github/workflows/run-bot-fast.yml`
  - keeps `RUN_MODE="normal"` for `app.cli`;
  - uses `FAST_WORKFLOW_MODE` and `HARIZON_FAST_RUN` for workflow speed profile;
  - logs key budget env values after quota/fast budget application.

- `scripts/apply_fast_run_budget.py`
  - no longer treats app `RUN_MODE=fast` as the signal;
  - raises balanced-fast depth defaults: odds-api.io 160 requests, target 220 matches,
    Bzzoiro 140 requests, SStats 100 requests;
  - adds extra odds-api.io env aliases used by different provider wrappers;
  - documents that app RUN_MODE is intentionally normal.

- `scripts/assert_fast_run_depth.py`
  - adds recommendations to the warning artifact when depth is too thin.

- `tests/test_fast_run_balanced_depth_v2.py`
  - regression coverage for app RUN_MODE normal + fast budget behavior.

## Safety

Publication guards are unchanged. Tier A still requires 2 independent odds
sources, SStats is still context-only, and EV/edge/xG/quality guards remain in
place.
