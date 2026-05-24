# HARIZON unified scheme follow-up: API coverage discovery soft guard

## Problem observed in run-bot-26370011900

The model found a positive candidate internally, but `api_coverage_consensus_runtime_patch`
hard-filtered it inside `CandidateFactory` because the exact market had only one independent
odds provider. That is correct for final publication, but too early for discovery/reporting:
`raw candidates` became 0, quality/fallback saw nothing, and Telegram did not explain the real
reject reason.

## Fix

- Add `app/services/api_coverage_discovery_soft_guard_patch.py`.
- Install it immediately after `api_coverage_consensus_runtime_patch` in `runtime_startup_chain.py`.
- It keeps strict final publish behavior unchanged, but lets discovery candidates continue with
  annotations when the only issue is missing exact odds/context source coverage.

## Expected next run

- `Raw/candidates before quality` should no longer drop to 0 when the model has a candidate.
- Controlled fallback/watchlist should show explicit reject reasons such as
  `api_coverage_missing_2_exact_odds_sources` or final Telegram odds-source guard.
- No single-provider candidate should be published because final publication guards are unchanged.
