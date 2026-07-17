# Run 29573278970 timeout audit

The July 17, 2026 run did not produce a fresh prediction cycle. The workflow job
was green because the shell step deliberately converts `timeout` into a diagnostic
status, but `.data/exports/latest-run-bot-step-status.json` contained:

```text
run bot failed or timed out with status 124
```

The discovery-first phase consumed 403.07 seconds before `PredictionRunner`
started. The largest repeated steps were:

- provider-day discovery: 106.71 seconds;
- inventory merge: 56.16 seconds;
- two SStats crosswalk passes: 74.55 seconds total;
- four target-expand passes: 90.48 seconds total;
- SStats deep enrichment: 46.13 seconds;
- Bzzoiro gap enrichment: 25.57 seconds.

This left too little of the 600-second shell budget for current odds collection,
candidate construction, quality filtering and autonomous ledger writes. Therefore
Telegram's line-guard counts came from persisted diagnostics and the displayed
`0/0` autonomous matrix was not a coverage measurement.

## Runtime policy introduced

- A same-day successful full discovery may be reused for six hours.
- Regular two-hour runs use a light incremental pass: inventory validation, cached
  SStats ID application and coverage-matrix rebuild.
- Full refreshes reserve prediction-run time with a 240-second preparation budget.
- Repeated target expansion is skipped when inventory already has at least 300
  rows.
- Optional deep SStats and Bzzoiro gap enrichment are skipped when they would
  consume the runner reserve.
- Per-event Bzzoiro metadata and prediction endpoints are blocked before they can
  consume the shared odds/stats request budget. Bulk Bzzoiro predictions remain
  enabled for prematch xG.
- Telegram explicitly marks status 124, stale candidate diagnostics and an
  unexecuted autonomous cycle instead of presenting them as current-run results.

No publication threshold, xG conflict guard, price-integrity rule, workflow or
external CronJob schedule is changed.
