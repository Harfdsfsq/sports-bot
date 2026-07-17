# Run 29571069927 timeout audit

The July 17, 2026 13:00 MSK report correctly rejected the two reserve candidates,
but the main `python -m app.cli run-once` process did not finish normally.  The
workflow status file contained:

```text
run bot failed or timed out with status 124
```

The retained log contained 765 HTTP calls over approximately 9 minutes 19
seconds.  Bzzoiro accounted for most of them:

- 136 event odds calls;
- 121 event stats calls;
- 121 event metadata calls;
- 121 event prediction calls;
- 80 event-detail calls;
- 80 odds-comparison calls;
- 6 event-list calls.

The v2 provider explicitly had no hard per-run limit, and the comparison patch
added another per-context request loop.  This allowed the provider stage to use
almost the entire 600-second shell timeout before normal candidate construction
and autonomous-ledger writes.

## Runtime policy introduced by the hotfix

The regular Python process now has one shared Bzzoiro v2 budget:

- 120 requests by default, with an absolute ceiling of 140;
- 150 seconds by default, with an absolute ceiling of 210 seconds;
- 48 kickoff-prioritized matches for event detail enrichment;
- up to 24 odds-comparison requests;
- 8-second HTTP timeout and no per-event retries;
- event odds and stats remain enabled;
- event metadata is disabled;
- per-event prediction is disabled because the bulk predictions provider remains
  enabled and supplies the pre-match model/xG payload more efficiently.

The output `.data/exports/latest-bzzoiro-runtime-hard-budget.json` reports the
actual claimed/denied request count, endpoint mix, elapsed time and stop reason.

## Candidate interpretation

The two Melbourne Knights — Brunswick City reserve rows were not contradictory.
They used different total points:

- Over 3.5 was rejected because total xG 3.348 implied only about 43.0% for the
  over, below the candidate probability;
- Under 2.5 was rejected because the same total xG implied only about 35.0% for
  the under.

A model total between 2.5 and 3.5 can correctly reject both tails.  Neither row
had two independent odds providers, and both came from the market-promotion
research path, so publication guards remain unchanged.
