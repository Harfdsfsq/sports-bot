# Autonomous accumulation mode

This mode is for a seven-day evidence-collection period before real-money use.
It does not alter the external CronJob schedule and does not require a workflow
change.

## What the run now does

1. Keeps the day inventory at up to 300 fixtures.
2. Prioritizes context enrichment by kickoff windows: 0–4h, 4–8h, 8–12h,
   12–16h, 16–20h, 20–24h, then 24h+.
3. Measures exact market-bucket coverage, not only “some odds exist”:
   two independent odds providers and two bookmakers must quote the same
   family/selection/point/team-side bucket.
4. Counts only core sporting contexts toward strict coverage. Weather and news
   are recorded, but they cannot by themselves satisfy the two-context contract.
5. Stores all constructed candidates before quality filtering and after quality
   filtering, including market-derived candidates that are not safe to publish.
6. Publicly allows only independently modelled `xg_total` and `xg_spread`
   candidates in totals/spreads markets, with 2+ odds providers, 2+ bookmakers,
   2+ core contexts, valid xG inputs, conservative probability shrinkage, positive
   post-shrink EV, and the existing line-movement lifecycle check.

## Generated artifacts

The run-bot workflow commits and uploads only flat `.data/exports/latest-*`
files. The ledgers therefore use bounded JSON arrays rather than a pruned
subdirectory/JSONL layout:

- `.data/exports/latest-autonomous-coverage-matrix.json`
- `.data/exports/latest-autonomous-coverage-run-ledger.json`
- `.data/exports/latest-autonomous-prediction-ledger.json`
- `.data/exports/latest-autonomous-accumulation-report.json`
- `.data/exports/latest-autonomous-persistence-policy.json`

The coverage matrix has L0–L3 levels. L3 means the match has the full strict
contract: 2+ exact odds providers, 2+ bookmakers and 2+ core contexts.

The coverage-run ledger retains 256 runs by default. The prediction ledger
retains 12,000 rows by default. These limits can be changed with
`AUTONOMOUS_COVERAGE_LEDGER_MAX_RUNS` and
`AUTONOMOUS_PREDICTION_LEDGER_MAX_ROWS`.

## Quota policy

- odds-api.io: 100 requests/hour per configured account (200 total with both
  project accounts), with batch endpoints preferred.
- SStats: 150 requests/run safety budget and historical team-form joins.
- Bzzoiro: 200 requests/run safety budget, cached event/subresource loading.
- SportLogic: 30 requests per regular run plus an 80-request 00:00 inventory
  reserve. This keeps twelve regular runs and the inventory below 500/day.

## Seven-day review gate

Do not treat a one-week sample as proof of profitability. At the end of the
accumulation period, settle every candidate and report at least:

- count, hit rate, flat-stake ROI and yield;
- closing-line value (CLV);
- Brier score and calibration by probability bucket;
- results split by `model_mode`, market family, league, odds band, evidence
  coverage and time-to-kickoff;
- maximum drawdown and confidence intervals;
- duplicate/correlated exposure by match and league.

Public real-money use should remain disabled or nominal until the independent xG
segments show positive CLV and stable out-of-sample ROI over a materially larger
sample. Market-derived/simple-market rows are research controls, not evidence of
alpha.
