# Account 1 bookmaker replacement — 11 November 2026

The user's current Odds-api.io selection screen marks Bet365 as Paid and Unibet
as Saved. Recent live entitlement discovery confirmed only Unibet for account 1.
Configure the daily pipeline explicitly with Unibet,William Hill instead of
inheriting Bet365,Unibet. William Hill appears without the Paid badge on the
provided screen and is documented by Odds-api.io as a supported football book.
This does not establish that every target match has a William Hill price.

Account 2 remains 1xbet,Betano. William Hill was already accepted in the
target/consensus filters. Preserve those compatibility filters, legacy provider
fallback defaults and historical bookmaker/settlement records, which can include
Bet365. They do not request bookmakers: the explicit account-1 request pair
replaces Bet365 with William Hill. No new subscription, API key, paid endpoint or higher quota is required
by this configuration change.

The user must select and save William Hill alongside Unibet in the first API
account's bookmaker dashboard. Repository configuration does not change that
remote account selection. The native pipeline reads GET /bookmakers/selected,
queries entitled selected books, and warns about configured books absent from
that response. Its six-hour entitlement cache includes configured_bookmakers;
the new explicit account-1 pair invalidates the old Unibet-only cached response
on the first run. Save the account selection before that run.

Validation: execute the exact credential-free workflow regression step with the
new daily_quality.env. Next live dry run should report selected_bookmakers and
effective_bookmakers containing Unibet and William Hill for account 1. Check
actual offers/book counts and coverage rather than claiming a guaranteed lift.
No local production credentials or remote account-selection mutation was used.
The scheduled production branch remains unchanged; use the development branch
fix/coverage-integrity-a-only for validation.

References:
- User-provided current bookmaker-selection screenshot.
- https://odds-api.io/sportsbooks/william-hill
- https://api.odds-api.io/v3/docs/index.html

Local verification completed: exact workflow environment, 456 tests passed;
actual Settings/provider account resolution returned Unibet,William Hill and
1xbet,Betano. Whitespace checks passed. Live account selection is not yet
confirmed and must be checked in the next artifact.
