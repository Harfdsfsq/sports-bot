# Development and validation operating plan

## Current evidence, not a profitability claim

Snapshot: run 43187710105, 12 November 2026 at 12:03 Moscow time.

The deployed native totals paper trial (`daily-goals-v4`) had 211 settled
observations: 94 wins, 106 losses and 11 pushes. Flat-unit paper PnL was
−15.459 and ROI −7.327%. Its Brier score was 0.24012 versus market 0.23408
(lower is better). Increasing the inventory alone does not repair this result.

The price-aware exploratory comparison had 710 settled fixtures over twelve
days. Its current-goals variant returned −4.19% across 479 selections; opponent,
shrunk and overdispersed variants returned +8.57%, +13.76% and +14.26% respectively.
These are exploratory comparisons, not evidence authorizing live publication.
Their paired probability-improvement intervals still include zero. Existing
financial accounting, losing observations and conflicting-result quarantines
are preserved. No past winner is re-labelled as a future observation.

## Implemented in this iteration

### Stable model cohorts

`stable-forward-models-v1` replaces the repeatedly numbered collector protocols.
Every newly priced forecast records its model hash, cohort ID and pipeline hash.
Declaration happens before its line selection is frozen. A change to the
registered prediction mathematics or selection policy starts a new model epoch
and archives the previous declaration. Old forecasts remain in history but
cannot be credited to a new algorithm or selected after observing their result.

Transport, retry and collection revisions are separately fingerprinted. They do
not erase the active cohort or its losses. The report exposes counts by pipeline
revision so a data-quality incident can still be investigated. The evidence
contract, pre-kickoff quote receipt, original timestamps, exact fixture/result
checks and one observation per fixture remain enforced. Previously unlabelled
experiments stay exploratory and cannot authorize live bets.

The primary predeclared research strategy remains `opponent`; we do not switch
the winner to whichever exploratory variant looks best today. It is still a
challenger, not the native algorithm used for Telegram publication. A validated
challenger requires an explicit matching implementation and its own release
review. A positive challenger report cannot release unrelated native forecasts.

### Country-scoped team catalogue discovery

Missing history identifiers can now be looked up through the documented
`GET /Teams/list` endpoint. A reviewed country prefix in the fixture's competition
and exact canonical name/squad (or an explicitly reviewed spelling alias) must
agree with a unique, complete catalogue response. Unknown countries, truncated
responses, near-name matches and ambiguous IDs are not accepted.

The additional catalogue budget is at most six physical requests, including
retries, and eighteen seconds per enrichment call, inside existing shared and
provider limits. Positive receipts cache for seven days; unresolved searches
cache for six hours so the next run can reach later fixtures. A team ID alone is
never counted as sporting context. The existing history request must return
enough actual regulation-time results, and the resulting rows must pass normal
context admission, including the discovered team IDs.

Primary documentation: <https://api.sstats.net/openapi/v1.json>.
Public check: `/Teams/list?Name=Arkadag&Country=TM&Limit=100` returned one exact
Turkmenistan team with ID 21232. This proves catalogue availability, not a new
production context or a profitable forecast.

### Visible native A/B paper results and system readiness

New native paper selections preserve their A/B quality tier and research
blockers. Old unclassified rows remain unclassified. The totals report shows
these cohorts separately and never retroactively assigns A labels to winners.

`latest-system-readiness.json` distinguishes provider/data health from betting
readiness. A successful workflow cannot hide weak joint coverage, negative native
paper performance or incomplete independent validation. This report cannot
promote a model, send a Telegram forecast or change a stake.

## User workflow

1. Run the existing workflow once on `fix/coverage-integrity-a-only` with
   `dry_run=true`, `price_watch_only=false`, `max_picks=2` after this update.
2. Keep the existing two-hour external schedule. Do not repeatedly trigger
   extra identical runs: that consumes the free quotas without creating new
   independent matches or outcomes.
3. Ask for a check after the first run. The engineering checks are declaration
   persistence, catalogue request accounting, accepted contexts, settlement
   completeness and absence of duplicate forecasts.
4. Next review: 13 November after 12:00 Moscow time. Review data stability over
   the preceding day, unresolved results and native/research paper observations.
5. Review the first seven days as an engineering and data-quality milestone.
   Keep the predeclared algorithm fixed during this period; repair collection
   incidents without selecting parameters from the same period's winners.
6. A profitability/release decision needs the existing minimums: 200 settled
   picks, 300 paired scores, thirty distinct match days and fifty verified closing
   price comparisons, plus positive uncertainty bounds and a matching native
   implementation. Reaching these counts may take longer than thirty calendar
   days. Neither one week nor any hit-rate target is promised.

All current runs stay paper-only. No guarantee of sustained profit is made.
No new paid provider, bookmaker account change or bank exposure was introduced.
General CI debt remains visible and is not fixed by hiding legacy failing tests.

## Local verification for this release

670 native regression tests passed. Full suite: 97 failed / 1,084 passed; the
failure node IDs match the previous local baseline (the 96 existing GitHub
failures plus the known local SportLogic embedded-fixture case). Ruff and mypy
on changed production modules passed. Bank accounting was not reset. The new
country-catalogue-to-history-to-admission path has a complete offline integration
test, and model/transport fingerprint tests preserve losses through collector
changes while excluding old winners after a mathematical model change.
