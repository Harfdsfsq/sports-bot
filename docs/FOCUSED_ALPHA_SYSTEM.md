# HARIZON Focused Alpha

## Objective

Focused Alpha replaces the fixed-coverage optimisation target with a decision target:

> Find a very small number of bets whose conservative, calibrated expected value is positive after uncertainty, market quality and portfolio risk are taken into account.

The system does **not** promise a bet every day and does not target a fixed hit rate, fixture count or profit. A correct daily result may be zero public bets.

## Why the previous target was inefficient

The previous daily planner retained 300 fixtures and spent provider quota attempting to move every active row toward two odds providers and two context providers. That is useful for coverage research, but it is not the same objective as finding the best bets:

- many low-tier fixtures have no realistic second context provider;
- an extra API request on an unmodellable fixture has low decision value;
- raw coverage counts do not measure calibration, closing-line value or portfolio profit;
- forced breadth consumes quota that could deepen the evidence for a small number of promising matches;
- a high raw model edge with weak source identity can be less valuable than a smaller, well-confirmed edge.

The broad inventory is retained as a discovery and identity ledger. It is no longer a provider-enrichment quota.

## Funnel

### 1. Discovery universe

Collect upcoming fixtures cheaply over the configured horizon. The universe can contain 300–500 events. At this stage the system primarily needs:

- canonical teams, competition and kickoff;
- provider fixture IDs;
- a first market snapshot where available;
- enough metadata to estimate whether deeper enrichment is feasible.

Discovery volume is not a success metric.

### 2. Adaptive focus cohort

`app.services.focused_alpha` ranks the discovery rows by expected information value. The default maximum is 100, with progressive per-run targets of 40, 70 and 100. There is no minimum.

The score rewards:

- explicit independent odds-provider identities;
- explicit independent context-provider identities;
- real bookmaker depth on the same market;
- hard xG/form/rating evidence;
- strong fixture identity and semantic-ledger matching;
- a useful time-to-kickoff window;
- matches one provider away from becoming fully modellable;
- a strongly shrunk historical league reliability prior.

It penalises:

- zero-evidence fixtures that would require several uncertain calls;
- youth, reserve and friendly matches;
- identity gaps;
- expired fixtures;
- competitions concentrated beyond a per-league diversity cap.

A small exploration lane prevents the system from permanently excluding competitions with little history. Exploration never creates a publication entitlement.

### 3. Provider allocation by expected marginal value

The daily coverage plan assigns providers only to the focused cohort. An explicit empty assignment is authoritative: a provider receives zero targets rather than silently falling back to the broad runtime list.

Typical priorities are:

1. Match already has one good context and one good odds source: seek the missing independent sources.
2. Match has deep market coverage but lacks hard xG/form: spend context quota.
3. Match has strong context but weak exact-market depth: spend odds quota.
4. Match has no reliable identity or no initial evidence: defer unless it occupies an exploration slot.

The goal is not to turn all rows into `2+ / 2+`. The goal is to maximise how many rows become genuinely decision-ready per unit of API quota and wall-clock time.

### 4. Candidate modelling

The existing model still builds market candidates, but public decisions are evaluated with a conservative lower probability:

```text
p_conservative = max(p_market_implied, p_model_adjusted - uncertainty_margin)
```

The uncertainty margin increases when evidence is missing:

- fewer than two independent odds sources;
- fewer than two independent context sources;
- no hard xG;
- proxy or missing quality;
- shallow bookmaker depth.

The decision score uses conservative EV rather than raw EV and also includes:

- canonical edge;
- calibrated confidence and raw quality;
- exact-market bookmaker quorum;
- source independence;
- hard xG direction sanity;
- confirmed line movement;
- strongly shrunk historical league reliability;
- market/model disagreement penalties.

### 5. Portfolio selection

The shadow board chooses at most two decisions by default and avoids:

- multiple selections on one match;
- excessive same-league concentration;
- filling a daily quota with weaker bets;
- republishing previously seen selections.

`publication_minimum_count = 0` is a permanent rule.

### 6. Publication safety

Focused Alpha initially runs in shadow mode:

```text
FOCUSED_ALPHA_LIVE_ENABLED=false
```

The current public pipeline remains A-only and requires:

- two independent odds sources;
- two bookmakers on the exact market/selection/line;
- two independent context sources;
- raw quality, not a reserve proxy;
- hard xG sanity for totals;
- confirmed line movement;
- current exact selected price;
- semantic duplicate and alias consistency;
- existing EV, edge, timing, stake and daily-risk guards.

B-tier remains a watchlist and cannot publish.

## Historical decision ledger

The existing historical exports are overlapping and incomplete. Focused Alpha therefore creates a canonical audit instead of learning directly from raw rows.

The audit:

- merges publication exports by semantic prediction identity;
- prefers the most complete settled record;
- recomputes flat one-unit PnL from result and odds;
- reports performance by league, selection and price band;
- measures missing model probabilities, source identity and closing prices;
- strongly shrinks small league samples;
- blocks automatic threshold tuning until the dataset is fit for purpose.

Artifacts:

```text
.data/exports/latest-focused-alpha-canonical-history.json
.data/exports/latest-focused-alpha-history-audit.json
.data/exports/latest-focused-alpha-cohort.json
.data/exports/latest-focused-alpha-decisions.json
.data/exports/latest-focused-alpha-runtime-policy.json
```

## Promotion from shadow to live

Live model-led publication should remain disabled until all of the following are demonstrated on settled, timestamped decisions:

1. At least 100 canonical settled bets.
2. Model/adjusted probability captured for at least 90%.
3. Closing price captured for at least 80%.
4. Exact provider and bookmaker identities captured for at least 90%.
5. Positive closing-line value after vig and timestamp validation.
6. Acceptable calibration by probability bucket, not only aggregate hit rate.
7. Positive out-of-sample yield after transaction/price assumptions.
8. Drawdown and concentration within the bankroll policy.
9. No material alias, stale-price or settlement-integrity defects.

The promotion decision must be based on an out-of-sample window. It must not be inferred from a handful of wins.

## Metrics that matter

Primary:

- closing-line value;
- Brier/log loss and calibration error;
- conservative expected value versus realised flat-unit yield;
- maximum drawdown;
- profit factor and yield by market/league/price band;
- source availability and match-identity precision;
- percentage of selected shadow decisions later rejected by price or movement integrity.

Secondary:

- hit rate;
- number of public bets;
- number of fixtures in discovery;
- raw provider coverage.

Hit rate alone is not an optimisation target: a strategy can have a high hit rate and negative yield when prices are too short.

## Operating defaults

```text
Discovery universe: broad, no success quota
Focused cohort: max 100, phases 40/70/100, no minimum
Public decisions: max 2/day, no minimum
Live mode: disabled initially
Public tier: strict A only
B-tier: watchlist only
Data horizon: broad collection, final publication inside 2 hours
```

## Non-goals

Focused Alpha does not:

- guarantee profit or a particular pass rate;
- manufacture a second source from fields of one provider;
- treat multiple bookmakers inside one API as multiple APIs;
- use market-implied xG as independent hard context;
- force publication when the best conservative EV is non-positive;
- automatically tune production thresholds from the current dirty history;
- maximise the number of enriched fixtures.
