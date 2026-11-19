# Native odds and team-history expansion — 11 November 2026

This change adds real quote retrieval and sporting history to the development
branch. It does not change A-tier thresholds, activate B-tier publication,
merge PR #87, or switch the scheduled production branch. Local validation has
no production credentials. Actual PARI entitlement and coverage remain unknown
until a fresh dry run; absence of permission is reported, not worked around.

## PARI through SStats

Use a separate native SStatsPariDailyProvider, not the legacy patched adapter.
The official current schema documents /Pari/matches, /Pari/match/{eventId},
NotStarted / WillStartSoon / Live / Finished statuses, currentOdds and
prematchOdds, plus isBlocked/isDeleted flags. Runtime discovery uses UTC dates,
upcoming=true, includeOdds=false, bounded pagination and both TotalCount spellings.
Server-capped short pages are not evidence that all data has been loaded.

Select up to eight uniquely identified fixtures after matching both teams,
squad, home/away orientation and kickoff within fifteen minutes. Recheck detail
ID, teams, time and pre-match status. Only currentOdds is parsed; never substitute
prematchOdds or /Odds/{gameId}, whose default is closing prices. Blocked/deleted
quotes and explicit sub-period markets are rejected. Successful HTTP receipt
is the quote observation time; there is no re-stamped detail cache.

Twelve physical requests per provider instance, hard cap sixteen, with four
reserved for fresh final confirmations when the cap permits. The shared SStats
IP governor still applies. The native parent deadline is sixty seconds;
individual network timeouts are eight seconds. 401/402/403/429 stops the batch.
The default SStats context request cap is unchanged; PARI has its own per-run
cap and shares the same public-IP pacing and endpoint refusal state.

PARI is allowed as a bookmaker in target/consensus filters. A default no-op
additional-offer hook in PredictionRunner preserves the legacy path; the native
daily runner uses it to merge and report extra real quote maps. Final publication
routes to the source that supplied the selected price and retains all exact
book/market/line, current-value, context freshness and kickoff checks. These
prices are not another sporting-context source or measured xG.

## Football-data independent history

A missing future schedule no longer prevents all form retrieval. For missing
contexts, cache the TIER_ONE competition catalogue, bind a unique competition
by reviewed name/area aliases and its current-season dates, then fetch FINISHED
season results and standings independently of a scheduled fixture record.
At most two competitions / six extra requests, within the existing provider
cap and ten-per-minute shared governor. Catalogue cache lasts one day; history
and table cache six hours. All cache receipt timestamps are retained.

Require real identified results from the requested competition, within the
verified season and before now. Reject unsupported/nonfinal/unknown-duration
scores; extra-time/shootout games require explicit regulation scores. Duplicate
IDs cannot inflate samples and conflicting records for one ID are excluded.
Both teams need actual compatible history. No neutral or opponent history is
invented for an unknown team, and no future fixture ID is synthesized.

The native chronological form builder supplies real sample counts with provider
football_data. A distinct verified_team_history mode admits the stored team/form
evidence without claiming a direct scheduled-fixture receipt. Verified standings
are retained as supplemental facts, without an unvalidated probability bonus.
Converted rows are never archived as SStats receipts: original football_data
provider identity is preserved. Historical calibration/picks are not regenerated.

## Persistent SStats team results

Store normalized, identified completed regulation results in
.data/provider_cache/sstats-team-results-v1.json, already covered by the workflow's
persistent cache. Keep at most one year for history, reusing only the most recent
120 days for live form. Reuse requires an ID/name/squad binding also present in
the current provider response. Incompatible squad IDs are refused.

Keep original receipt timestamps per archived row. An incoming older cached
receipt cannot overwrite a newer one. Conflicting observations in the same batch
are marked unusable. Archive reads do not re-stamp evidence: derived form exposes
its original newest used receipt and oldest history receipt separately. The
existing fetch receipt remains the source timestamp when only fresh bulk rows
are used. New queries still verify current teams; completed old results are not
represented as newly downloaded results. The targeted supplement also feeds this
archive, so later runs can reuse its history without another extension request.

## Optional The Odds API backup

Prepared a native totals-only reserve enabled only by THE_ODDS_API_KEY. The
workflow reads the corresponding optional GitHub secret; no key is embedded.
Without it, no request is sent. OddsPapi is not implemented in this change;
its separate registration, key and 250-request monthly allowance are unnecessary
to exercise the existing-key PARI path first.

The backup targets current line gaps in six explicitly mapped major leagues.
Each call requests one UK region and one totals market, keeping the documented
cost at one credit. Two league calls per discovery batch; at most four calls per
instance including final confirmations. Shared local ceilings: eight/day,
500/month, ten/minute. Server x-requests-remaining can block further calls until
the next month. Existing external use of the same key is respected through that
header. No historical or paid endpoint is called. Ambiguous fixtures, wrong
squads, stale bookmaker snapshots (>15 minutes), invalid prices and non-total
markets cannot produce offers. The existing target-book filters still apply.

## Verification and next run

The exact embedded workflow regression step, with current daily_quality.env,
a temporary repository and stripped credentials: 482 passed. New modules/tests
pass Ruff; compilation and whitespace checks pass. Tests cover denied/zero-budget
PARI, identity/status/blocked/closing-only/sub-period rejection, empty-schedule
football form, youth/future/extra-time/competition ambiguity, cache receipts,
archive reuse, one-credit backup parameters, server quota exhaustion, and final
price routing. Full legacy CI remains unresolved and is not claimed green.

Next dry run on fix/coverage-integrity-a-only: inspect source_stats.sstats_pari
(HTTP statuses, blocked_status, accepted fixtures, requests, skip_reasons),
football_data.team_fallback, sstats.team_result_archive and the actual near-window
line/context intersection. Successful fake-client tests are not proof of real
key entitlement, increased coverage or positive forecast returns.

The existing quote-only CLV watcher still uses Odds-api.io. PARI-specific closing
references need a separate follow-up before claiming comprehensive PARI CLV;
missing same-book prices must remain missing, never substituted from another book.

Official references:
- https://api.sstats.net/openapi/v1.json
- https://docs.football-data.org/general/v4/competition.html
- https://www.football-data.org/pricing
- https://the-odds-api.com/liveapi/guides/v4/index.html
- https://the-odds-api.com/
