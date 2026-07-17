# Run 29576966686 timeout audit

The truthful timeout reporting introduced by PR #54 worked: the Telegram message
correctly stated that the main prediction cycle did not complete and that the
candidate, line-guard and fallback numbers could be persisted diagnostics.

The run still ended with status 124. The artifact shows that the incremental
preparation itself is no longer the bottleneck:

- discovery-first mode: `runbot_discovery_first_prepare_v5_incremental_reuse`;
- preparation duration: 5.32 seconds;
- full same-day discovery reused at age 21.64 minutes;
- repeated target expansion, provider discovery/merge, SStats deep enrichment and
  Bzzoiro inventory-gap enrichment were skipped.

## New bottlenecks

The runner started at 11:31:33 UTC. After odds-api.io downloaded ten event pages,
there was a 250.49-second gap before the first `/odds/multi` request. The provider
was matching approximately one thousand events against the entire active match
list with the full fuzzy scorer, producing O(events * matches) expensive text
comparisons.

Bzzoiro then claimed 120 requests and spent 254.16 seconds despite a nominal
150-second wall budget. The claim guard checks time before each request, so
already-claimed sequential requests can overshoot the wall limit. The useful
endpoint mix was three event-list calls, 58 odds calls and 59 odds-comparison
calls. Metadata and per-event prediction were successfully blocked, but the
remaining detail layer still consumed too much of the 600-second runner budget.

The autonomous persistence installer was active, but the coverage matrix and
prediction ledgers were absent because candidate/quality execution was never
reached.

## Hotfix policy

- exact and loose odds-api.io matches retain the existing scorer;
- fuzzy scoring receives only a kickoff/token shortlist of at most 24 matches;
- unmatched unrelated events return without scanning every inventory row;
- Bzzoiro detail enrichment is limited to 48 claims, 24 prioritized matches and
  12 comparison calls;
- a true outer coroutine deadline returns control after at most about 82 seconds;
- bulk Bzzoiro predictions remain enabled for prematch model/xG data;
- publication thresholds and source, value, xG, movement and price-integrity
  guards are unchanged.
