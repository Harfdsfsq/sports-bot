# API runtime diagnosis — 2026-04-30 run

Source: uploaded GitHub Actions log archive `logs_66923412981.zip` and repository branch `fix/sportlogic-api-providers`.

## Executive summary

The APIs are not failing as a group. The latest inspected run shows most successful HTTP calls returning 200. The problem is runtime orchestration:

- key providers are disabled by request-budget spacing before the runner starts;
- some providers fetch data but cannot match it to the odds-api.io match universe;
- daily inventory reporting is overwritten by tomorrow warm-up summary after a successful current-day merge;
- api-football is intentionally disabled but is still present in older human reports;
- SportLogic was added as a provider adapter, but the workflow/budget layer still needs a direct env/budget grant path so the provider can receive `SPORTLOGIC_API_KEY` during GitHub Actions runs.

## What the run actually did

- Runtime matches: 75
- Matches with offers: 41
- Contexts built: 32
- Candidates before quality: 3
- Raw candidates after quality layer: 1
- Publishable: 0

The no-pick outcome was not caused by API outage. The strongest borderline candidate was rejected by the duplicate guard because it had already been sent earlier.

## Provider-by-provider diagnosis

### odds_api_io

Status: working.

Evidence from logs:

- 2 event requests succeeded.
- 16 odds requests succeeded.
- 100 events fetched.
- 75 events matched exactly.
- 3,447 offers parsed.
- 41 matches ended with usable offers.
- Both accounts were active: account1 `Bet365,Unibet`; account2 `Betfair Exchange,Sbobet`.

Main issue: not a connectivity problem. The source works, but many matched events are not strong enough after quality gates or do not have enough independent context confirmation.

### SStats

Status: HTTP works, matching is noisy.

Evidence from logs:

- 23 requests, all HTTP 200.
- 31 contexts built.
- 18,837 unmatched historical rows scanned.
- Exact/loose/fuzzy match counters were 0 while contexts still appeared through provider fallback/team-form logic.

Root cause:

- The provider is over-scanning large historical windows and then relying on fallback aggregation.
- Matching against odds-api.io teams/leagues is weak for many leagues.
- This creates useful context for some teams but poor diagnostic clarity and wasted calls.

Recommended fix:

- Cache SStats team-history index per day.
- Add alias expansion from odds-api.io team names to SStats names.
- Report separately: `team_form_contexts_built`, `exact_event_contexts_built`, and `unmatched_rows_rate`.

### Bzzoiro

Status: API works, coverage is naturally narrow.

Evidence from logs:

- 2 requests, HTTP 200.
- 18 events and 18 predictions fetched.
- 2 contexts built.
- 1 exact + 1 fuzzy match.
- Several predictions rejected by league mismatch.

Root cause:

- Bzzoiro covers a smaller match set than odds-api.io.
- It is useful as a high-quality confirmation source, not as a broad coverage provider.

Recommended fix:

- Keep Bzzoiro enabled with high per-run cap.
- Prioritize odds-backed matches first.
- Improve league alias matching for continental/country naming differences.

### football_data

Status: API works, but low overlap with run universe.

Evidence from logs:

- 1 request, HTTP 200.
- 8 matches fetched.
- 0 events matched.
- 0 contexts built.
- Response contained only restricted `TIER_ONE` competitions for the queried date range.

Root cause:

- football-data.org free/registered plan returns a narrow competition set.
- The returned competitions often do not overlap with the long-tail odds-api.io schedule.
- The odds field says the Odds Package must be activated, so this source should be context-only, not odds.

Recommended fix:

- Use football_data only for top competitions and standings/history.
- Do not spend recovery grants on it when current odds-backed matches are mostly low/mid-tier leagues outside football-data coverage.

### TheSportsDB

Status: API works, low match/context yield.

Evidence from logs:

- 7 requests, all HTTP 200.
- 6 league tables fetched.
- 2 contexts built.
- 9 missing table rows.

Root cause:

- Useful for tables where league/team mapping is known.
- Poor for lower-tier or non-mainstream league names coming from odds-api.io.

Recommended fix:

- Add league alias map and team alias cache.
- Use it after odds-api.io shortlist rather than trying to cover all matches.

### OpenFootball

Status: enabled, but ineffective for this run.

Evidence from logs:

- 3 requests.
- All returned 404.
- 0 datasets loaded.
- 0 contexts built.

Root cause:

- The generated dataset paths such as `2025-26/no.1.json`, `2026/no.1.json`, `2025/no.1.json` do not exist for the requested competition/season combination.

Recommended fix:

- Add a 404 cache and skip repeated missing competition/season combinations for 24h.
- Expand valid competition map only for known openfootball dataset keys.

### AllSportsAPI

Status: disabled by runtime budget, not tested in the actual run.

Evidence from logs:

- `ALLSPORTSAPI_ENABLED=false`.
- `ALLSPORTSAPI_PER_RUN_MAX=0`.
- reason: `spacing_active:16.3m/120m`.
- provider_status: disabled_by_config.

Root cause:

- The budget governor disabled it before the runner started.
- The key may exist, but the provider is blocked by spacing policy.

Recommended fix:

- Treat AllSportsAPI as a periodic enrichment source, not every run.
- If user wants it used more aggressively, reduce spacing to 60m or allow only manual/run-recovery slots.

### FutrixMetrics, GNews, NewsAPI/Currents, OddsPapi, OddsFeed, Sportsbook API

Status: mostly disabled by spacing, daily/monthly caps, or policy.

Evidence from logs:

- FutrixMetrics: `spacing_active:16.3m/60m`.
- GNews: `spacing_active:16.3m/60m`.
- OddsPapi: `spacing_active:189.6m/720m`, also already above daily safe budget in the report.
- OddsFeed/Sportsbook API: spacing active.

Root cause:

- Request budget is doing what it was configured to do.
- These providers are not broken; they are intentionally throttled.

Recommended fix:

- Do not expect them every 2-hour run unless safe budgets are raised.
- Add a provider rotation report that separates `disabled_by_budget` from `broken`.

### Weather

Status: disabled because no usable weather budget/key was available in this run.

Evidence from logs:

- Run report showed `weather: missing_api_key`.
- Env also showed `WEATHERAPI_REQUEST_BUDGET_GRANTED=0`, `OPENWEATHERMAP_REQUEST_BUDGET_GRANTED=0`.
- OpenWeatherMap was marked daily-budget exhausted.
- WeatherAPI was marked daily-budget exhausted in env.

Root cause:

- The report simplified the reason to missing key, but the actual policy also zeroed the budget.
- Weather has no calls because both per-run caps were 0.

Recommended fix:

- Add reason priority in report: `budget_zero` / `daily_budget_exhausted` before `missing_api_key` when caps are 0.
- If weather is important, set a real `WEATHERAPI_KEY` and reduce daily weather probing or use Open-Meteo/public weather for venue-less fallback.

### api-football

Status: intentionally removed/disabled.

Evidence from logs:

- `ENABLE_API_FOOTBALL=false`.
- `API_FOOTBALL_KEY=` blank.
- `API_FOOTBALL_PER_RUN_MAX=0`.
- budget reason: `removed_from_project`.

Root cause:

- This is not an API failure. It is intentionally disabled by policy and should be removed from reports.

Recommended fix:

- Keep it removed from runtime.
- Remove it from detailed provider lists and quota summaries.

### SportLogic

Status: adapter added in code, but not active in inspected run.

Root cause:

- The inspected run happened before SportLogic was fully wired.
- Workflow did not expose `SPORTLOGIC_API_KEY` to the Python process.
- Provider budget policy did not yet include SportLogic as a grantable provider.

Required secret:

- `SPORTLOGIC_API_KEY`

Recommended fix:

- Add `SPORTLOGIC_API_KEY: ${{ secrets.SPORTLOGIC_API_KEY }}` to `.github/workflows/run-bot.yml`.
- Add SportLogic to provider budget policy with per-run cap and daily safe budget.
- Ensure detailed reports include `sportlogic` and `sportlogic_context` source stats.

## Inventory/reporting bug

Current-day inventory was actually good after build/merge:

- 201 total matches
- 101 with odds
- 101 with context
- 101 ready for model

But the final detailed report showed:

- 9 total matches
- 0 with odds
- 0 with context
- 0 ready

Root cause:

- Next-day warm-up rebuilt `2026-05-01` inventory with 9 matches and wrote `latest-day-inventory-summary.json`.
- It restored aliases to current day, but did not restore the current-day summary file.
- Detailed report read the stale tomorrow summary.

Recommended fix:

- After `merge_run_coverage_into_day_inventory.py`, rewrite `latest-day-inventory-summary.json` from current-day runtime coverage counts.
- Never allow tomorrow warm-up summary to drive the current run report.

## Prioritized fixes

1. Directly expose SportLogic key in workflow and add it to provider budget policy.
2. Remove api-football from detailed report provider lists.
3. Fix inventory summary restoration after tomorrow warm-up.
4. Add provider state categories: `works`, `disabled_by_budget`, `missing_key`, `low_overlap`, `parser/matching_issue`.
5. Improve matching/aliasing for SStats, Bzzoiro, football_data and TheSportsDB.
6. Add OpenFootball 404 cache to stop repeated dead dataset probes.
7. Adjust weather report reason priority so budget exhaustion is not reported as only missing key.

## Bottom line

The main live source, odds-api.io, is working. SStats and Bzzoiro are working but need better matching and better diagnostics. Several other providers are not broken; the runtime budget policy disables them before the runner. The biggest current blocker is orchestration/reporting: provider budget, SportLogic env exposure, and day inventory summary restoration.
