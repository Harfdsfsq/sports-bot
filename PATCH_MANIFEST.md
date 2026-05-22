# HARIZON speed-run patch

Adds a separate `run-bot-fast` GitHub Actions workflow and two helper scripts.

## What speeds up

- Uses `actions/setup-python` pip cache.
- Skips provider smoke and tests by default; they are still available via workflow inputs or `mode=full`.
- Trims oversized cached day inventory back to the publication target before the run.
- Applies lower discovery budgets for expensive providers in fast mode.
- Automatically disables SportLogic in fast mode when recent artifacts show zero matched/offers or active-core exclusion.
- Commits only small lifecycle state by default instead of the full `.data/exports`, `.data/cache`, and inventory tree.
- Uploads artifacts with low compression for faster upload.

## Safety

Publication guards are unchanged:

- SStats remains context-only.
- Tier A still requires 2 independent odds sources.
- Telegram publication still requires EV/edge/xG/quality guards.
- Fast workflow can be bypassed by using the existing `run-bot` workflow or selecting `mode=full`.
