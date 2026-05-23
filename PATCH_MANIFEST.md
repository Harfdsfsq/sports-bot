# sports-bot fast balanced depth v4 fix

## Why
The fast workflow was running, but odds-api.io event discovery hit `429 Too Many Requests` before odds backfill. Because the provider uses the first account for event lookup, a short cooldown on account1 caused `odds req=0`, `2+ books=0`, and no odds-api/Bzzoiro overlap. Bzzoiro odds were present, but its totals were materialized as `15/25/35`, which CandidateFactory treated as unsupported totals instead of `1.5/2.5/3.5`.

## Changes
- `scripts/apply_fast_run_budget.py`
  - balanced profile bumped to `balanced-depth-v4`.
  - sets `ODDS_API_IO_FAST_EVENT_ACCOUNT=account2` and `ODDS_API_IO_ACCOUNT_ORDER=account2,account1` when the second key is present.
- `.github/workflows/run-bot-fast.yml`
  - logs the effective fast event account.
- `app/providers/odds_api_io_fast_event_account_patch.py`
  - fast-mode runtime patch: reorders odds-api.io accounts so account2 can fetch events while both accounts still fetch odds.
- `app/services/bzzoiro_total_point_normalization_patch.py`
  - normalizes Bzzoiro totals `15/25/35` to `1.5/2.5/3.5` before exact-offer bridge builds Offer rows.
- `app/services/runtime_startup_chain.py`
  - installs the odds-api.io fast event-account patch before provider use.
  - installs Bzzoiro point normalization before exact offer bridge.
- `scripts/assert_fast_run_depth.py`
  - detects odds-api.io 429 before odds backfill and writes a specific warning/recommendation.
- `tests/test_fast_v4_runtime_patches.py`
  - regression tests for account order and Bzzoiro point normalization.

## Safety
No publication guard is weakened. SStats remains context-only. Tier A/Tier B source, EV, edge, xG and quality guards are unchanged.
