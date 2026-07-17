# Run 29579992581 timeout audit

## Verdict

The workflow checked out PR #55's merge commit, but the main `run-once` process
still ended with status 124. The Telegram report correctly treated candidate,
line-movement and fallback rows as potentially stale and did not present the
missing autonomous matrix as zero coverage.

## What PR #55 fixed

The indexed odds-api.io matcher processed 999 provider events in 29.491 seconds.
It reduced 216,306 possible inventory comparisons to 2,764 shortlisted comparisons
(98.72% reduction), with a maximum shortlist of 24. The previous roughly
250-second all-inventory fuzzy scan is no longer the dominant bottleneck.

Per-event Bzzoiro metadata and prediction endpoints also remained disabled.

## Remaining defects

### Full/incremental preparation alternates

The preparation was full again and consumed 200.89 seconds. The previous run's
incremental result had overwritten `latest-runbot-discovery-first-prepare.json`.
`previous_full_prepare()` reads only that file, so it could no longer see the
successful full refresh from less than two hours earlier. This causes a full run
to be followed by an incremental run and then another full run.

The fix stores the last successful full result separately in
`latest-runbot-discovery-first-full-prepare.json`. Incremental runs continue to
update the normal latest report but do not replace the full checkpoint.

### Provider-level deadline was bypassed

The Bzzoiro hard-budget report recorded 48 claimed requests, 147 denied attempts,
zero contexts, and 185.498 elapsed seconds. The provider-deadline report, however,
said it completed in 0.187 seconds. This proves that the deadline wrapped a short
or later provider method after another runtime wrapper had replaced the long
production `fetch_context` path.

The fix enforces the deadline at `PredictionRunner._fetch_provider`, immediately
before the runner instance is created and after preflight/provider wrappers have
finished installing. The Bzzoiro context call is cancelled after 55 seconds by
default and returns an explicit degraded empty result so the other providers,
CandidateFactory, quality stage and autonomous ledgers can continue.

## Safety

No publication threshold, source quorum, bookmaker quorum, xG-direction rule,
line-movement rule, value threshold, price-integrity rule, workflow schedule or
external CronJob schedule is relaxed.
