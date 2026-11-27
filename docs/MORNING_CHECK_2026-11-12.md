# Morning run review, 12 November 2026

Five consecutive runs of SHA `d11a3aa` completed successfully at a two-hour
cadence. Times below are Moscow time, rounded to the dispatch hour. These
changing windows are not a fixed cohort and must not be interpreted as model ROI.

| Dispatch | Inventory | Fresh daily pairs | Near fixtures | Near line | Near context | Near pairs |
|---|---:|---:|---:|---:|---:|---:|
| 02:00 | 150 | 32 | 23 | 8 | 17 | 4 |
| 04:00 | 157 | 31 | 4 | 1 | 2 | 1 |
| 06:00 | 162 | 40 | 3 | 3 | 1 | 1 |
| 08:00 | 162 | 33 | 8 | 7 | 3 | 2 |
| 10:00 | 167 | 2 | 18 | 12 | 0 | 0 |

Runs, in the same order:

- <https://github.com/Harfdsfsq/sports-bot/actions/runs/43116467339>
- <https://github.com/Harfdsfsq/sports-bot/actions/runs/43131481609>
- <https://github.com/Harfdsfsq/sports-bot/actions/runs/43145291379>
- <https://github.com/Harfdsfsq/sports-bot/actions/runs/43158508338>
- <https://github.com/Harfdsfsq/sports-bot/actions/runs/43172148707>

## What worked

The 02:00 run recovered DC Power and América lines, verified against women's
competition labels. Their bookmaker responses included correct kickoff and
team identities. PARI had no request errors across these five runs. The main
workflow and three specialized CI checks succeeded. General CI still failed
its full test stage: 96 failed / 1,065 passed; lint, mypy on 46 files and imports
passed. Workflow completion alone does not mean usable betting coverage.

## Main failure

SStats reached the outer 300-second provider deadline at 04:00 and 10:00. The
runner then replaced the entire response with an empty result and a generic
deadline flag. It also lost the in-progress request counters and diagnostic
stage. At 10:00, 66 contexts were deferred and valid previous contexts had
mostly expired. No near-window sporting context survived, despite twelve lines.
The displayed one SStats request belonged to subsequent optional standings
enrichment, not the cancelled history collection's complete request count.

## Repair

- Bound initial daily history collection to 90 seconds, inside the existing
  300-second outer budget, leaving time for ID-targeted enrichment and form.
- Fetch recent date windows first, rather than starting with the oldest week.
- Checkpoint every received history page, including an incomplete window.
  Partial pages are not written as a complete history cache.
- On the initial history deadline, build from completed pages and continue the
  bounded targeted stage; unresolved matches are deferred, not negative-cached.
- Checkpoint completed contexts before optional enrichment. If the outer deadline
  fires later, return those contexts with original source observations, actual
  counters and the last completed stage. Normal identity, squad, team-ID, history
  cutoff and freshness admission still applies. Old observations are not redated.
- V8 declares the changed collector input rules. V7 and its single lost paper
  outcome remain in archived validation history; they are not erased or credited
  as V8 results. No live gate, stake or bookmaker selection was relaxed.

## Financial and data limits

All five runs were dry runs and published zero Telegram forecasts. V7 accumulated
one settled prospective paper bet: a loss of one paper unit. This is insufficient
to establish an edge. Recorded bankroll stayed 973.84 units, with no open exposure.
Inventory is 167, below the 300 goal; current coverage is far below 90–95%.
The NCAA metadata audits still returned no usable bookmaker count, so no evidence
supports a specific replacement bookmaker yet.

## Next validation

The next dry run must show `initial_history_timeout`, `initial_history_partial_rows`,
`last_completed_stage` and, if the outer deadline fires, `salvaged_contexts`.
Compare accepted fresh sporting contexts and near pairs, not just workflow status.
Local regression tests explicitly reject salvaged seven-hour-old observations
and preserve valid two-hour-old receipts through the real runner admission path.

Local checks: 655 native regression tests passed; Ruff and changed-module mypy
passed. Full local suite: 97 failed / 1,069 passed. The failure set contains the
96 existing GitHub failures plus the previously known local SportLogic embedded
fixture failure; no new production regression is claimed fixed by that suite.
