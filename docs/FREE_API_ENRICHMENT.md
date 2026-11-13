# Native free API enrichment — 10 November 2026

This supplements the existing daily pipeline. No new accounts, subscriptions,
keys or higher provider quotas are introduced. Local verification has no live
keys; real coverage must be measured in a fresh branch workflow artifact.

## Bzzoiro v2

The native v1 integration remains, with a bounded v2 supplement using the
shared BudgetedAsyncClient. It requests paginated /api/v2/predictions/ for the
near window, normalizes documented nested fields, and admits only a unique
verified home/away/squad/time identity with a positive event ID. Explicit
non-upcoming statuses are rejected. No confidence-ranked choice between
ambiguous fixture/model records. Predictions without explicit sporting goal
projections do not become inferred market-derived xG.

Freshness uses updated_at when supplied, falling back to created_at for older
payloads. Original created_at is retained separately. The API says created_at
is immutable; treating it as last recalculation incorrectly rejects refreshed
predictions. Market probabilities are 0–100 and converted explicitly; model
confidence is 0–1. Predictions/goal projections are not measured shot-level xG.

For admitted fixtures the supplement reads /events/{id}/ after validating its
returned ID, team pair and kickoff. It retains event venue, weather, neutral
venue, derby/travel/round/group facts where actually supplied. Venue flows to
the existing verified-fixture weather path; no guessed stadium or home city.

Optional /availability/ gates confirmed lineups. Predicted lineups are not
labelled confirmed. /h2h/ is retained as supplemental history. A league table
uses explicit league and season IDs from the verified event, not a guessed
current season. Flat and grouped tables require both team IDs in one unique
table/group. These optional facts are stored for audit/research; they do not
add an unvalidated probability, quality bonus or another independent source.

Limits: 8 near-window matches, 24 requests by default (hard maximum 30), shared
native-provider per-run budget and daily/minute governor also apply. The
supplement has a 60-second deadline and <=8 seconds per call; a 401/402/403/429
stops new calls for that supplement. Cache TTLs: predictions/availability and
lineups 10 minutes, detail 1 hour, table 2 hours, H2H 6 hours. Cached prediction
receipt time is retained, never re-stamped as new evidence. Native v1 contexts
are preserved if optional enrichment fails.

**Free odds limitation:** Bzzoiro documents consensus-only prices for free keys.
bookmaker_slug=consensus is not a bookmaker. This supplement never creates an
Offer from those prices, never adds bookmaker_count to quorum, and never calls
paid /odds/comparison/, /odds/best/ or bookmaker-filtered endpoints. Executable
prices and final same-book confirmation continue through entitled Odds-api.io.

## SStats

The runtime history loader now advances by the actual returned page length;
a server-capped short page is not proof of end-of-data. Empty response, trusted
large total, repeated page or existing request budget terminates collection.
Repeated pages mark the history incomplete and do not create a complete cache.
Native history cache namespace v5 avoids trusting v4 pages that may have been
truncated by the old implementation. Overlapping-window reuse, source timestamps,
regulation-score selection and squad checks remain tested. The first run may
need more history requests; shared 30/minute local pacing and run cap remain.

## Verification and next run

The exact critical workflow step is run locally with daily_quality.env loaded,
a temporary repository copy and credentials stripped. Contract tests cover v2
units/freshness, exact identity and youth rejection, table ID pairing, cache
reuse, permission/quota refusal, zero request allowance, invalid sporting data,
and capped/repeated SStats pages. This is not verification of live provider
entitlements or achieved 90–95% coverage.

In source_stats.bzzoiro inspect v2_native: requests, cache_hits, contexts, details,
standings, rejected, errors and blocked_status. Inspect admitted context payloads
and latest-actionable-coverage-gaps.json. New facts may legitimately be missing.
The overall A-tier, historical/EV and bankroll contracts are unchanged.

Official references consulted:
- https://sports.bzzoiro.com/docs/football/events/
- https://sports.bzzoiro.com/docs/football/odds-predictions/
- https://sports.bzzoiro.com/docs/football/leagues/
- https://sports.bzzoiro.com/docs/conventions/
- https://api.sstats.net/docs/
- https://sstats.net/api

## Targeted history follow-up after run 42823878703

The first live dry run completed successfully but the broad SStats extension
hit its 60-second deadline after 19,731 partial historical rows and improved no
near-window samples. Native Bzz v2 made one request and built no contexts;
its former counters did not distinguish empty discovery from unmatched data.
These observations do not establish a provider entitlement or identity failure.

SStats now first extends thin near-window form using documented Games/list
parameters Team (comma-separated IDs, either side), Ended=true, Order=-1,
From/To (exclusive To), TimeZone=0, Limit/Offset. IDs must be witnessed with
compatible names and squad in the initial native rows. Ambiguous names across
IDs and IDs spanning incompatible squads are refused. Up to eight fixtures,
120 days and four physical requests (hard cap six), including retries, are
allowed; the shared provider budget and a 25-second deadline also apply.

Returned rows must contain a bound ID with the right name/squad, positive game
ID, past kickoff, completed status when supplied, and finite, nonnegative
integer regulation scores. Exact game copies cannot inflate samples; conflicting
records for one game ID are excluded. Native form chronology and cross-record
score conflict checks still apply. Targeted rows alone build replacement form;
a context replaces the old form only if the smaller team sample improves.
The change does not turn form-derived projections into measured shot-level xG.

Complete targeted responses are cached for 60 minutes with their original
receipt time. Short pages continue until empty, repeated page, deadline or
budget; incomplete responses are usable after validation but are not cached as
complete. The configured broad extension is disabled to avoid another world
history scan; it remains an explicit opt-in compatibility path. Missing team
IDs stay visible as no_unique_team_ids rather than being guessed.

source_stats.sstats.targeted_history and the human-readable report expose
bound_teams, requests, rows, rejected_rows, conflicting_ids, cache_hits, contexts,
improved and stop_reason. Bzz v2 now exposes response_shape, prediction_records,
fixtures_matched, skip_reasons and limited fixture/field-name samples, without
credentials or full API bodies. The table integration also recognizes documented
home_team_obj/away_team_obj IDs only after checking each object's team/squad.
Documented detail flag names are retained alongside legacy names.

Official SStats parameter contract: https://api.sstats.net/openapi/v1.json
Actual coverage lift remains unmeasured until the next fresh workflow run.

### SStats timestamp wire format

Run 42871752744 returned HTTP 400 for the targeted To value with Python's
microsecond suffix. Use UTC ISO timestamps with second precision
(YYYY-MM-DDTHH:MM:SS+00:00), preserving To's exclusive bound. The native form
integration test now checks this exact wire shape in addition to semantic
identity and score contracts. A successful workflow does not imply a successful
optional provider request; inspect targeted_history and native http_statuses.
