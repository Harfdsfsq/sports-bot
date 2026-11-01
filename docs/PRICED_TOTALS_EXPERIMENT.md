# Price-aware totals experiment and operating procedure

Implemented 2026-11-01 on feature/daily-quality-pipeline. This is a prospective
experiment, not a claim of profitability. Existing daily-goals-v4 probabilities,
published financial history and historical risk blocks are preserved.

## Production changes

* A/B counts are no longer a selection objective. After all existing eligibility
  checks, descending EV is the primary ranking; data quality breaks ties.
* Immediately before forecast delivery, fetch the exact fixture again with the
  budgeted Odds-api.io client (20-second deadline). Confirm the same bookmaker,
  full-time totals line and side, and a receipt timestamp from this request.
* Reject missing, stale or worse prices. At a better price, retain the original
  conservative advertised price and recompute its EV; do not silently upgrade
  a selection without rebuilding the model contract. Recheck kickoff lead and
  context expiry. No alternative bookmaker substitution and no forced retry.
* Check confirmation age again before sending. Recompute the bankroll preview
  after removing candidates that failed confirmation. Telegram acknowledgements
  remain the authority for recording actual publications.

## New experiment: priced-totals-v1

Each qualifying fixture is frozen once, before kickoff (30 minutes–4 hours).
It needs a verified team sample of at least six, an admitted sporting context,
an opponent-model projection, and fresh paired over/under prices from at least
two bookmakers. Supported lines: integer/half totals 1.5–5.5; full regulation
time only. Both sides are paired within the same bookmaker; no cross-book vig
removal. Books are Bet365, Unibet, 1xbet and Betano when actually available.

All six models see exactly the same lines and prices:

1. Market: proportional margin removal per book, then mean fair probability.
2. Current goals: Poisson from the current sporting context's goal estimates.
   This is the raw goal component, NOT a replay of v4's calibrated strategy.
3. Opponent: attack/defence Poisson model already fit without future data.
4. Shrunk: opponent estimate shrunk toward a time-weighted league total using
   effective team sample sizes and fixed prior strength 20.
5. Overdispersed: negative-binomial challenger with the shrunk mean; dispersion
   estimated only from earlier league goals and bounded at 0.5. Heterogeneity
   can inflate this estimate; prospective comparison determines whether it helps.
6. Calibrated: a regularized logistic correction around market probability,
   trained from this protocol's earlier observed results.

For every model, enumerate the frozen lines/sides, require odds 1.5–3.2 and
expected return >=3%, and select the highest EV, at most one bet per fixture.
No qualifying line means no hypothetical bet. Integer-line EV includes pushes.
These are unit-stake simulations, never entered in the bankroll or Telegram
forecasts. Each model may pick a different line, which is the strategy being tested.

Probability comparisons use the SAME predetermined anchor line nearest 2.5,
with ties toward the lower line, regardless of which line a model selected.
Pushes are excluded from conditional-probability scoring, not counted as losses.
The market model uses the shrunk-model push mass only for integer-line EV;
its conditional win probability remains entirely market-derived. This limitation
must be considered in comparisons of integer-line strategies.

## Temporal calibration

No training until 100 decisive, resolved anchor forecasts from this protocol.
Outcome observation time must precede calibration time. Earliest 75% of ordered
kickoffs train the logistic correction; later 25% validate. Equal kickoffs never
cross the boundary. At least 70 training and 25 validation fixtures are required.
Only a correction improving validation log loss over the market is retained.
Otherwise alpha/intercept are zero (market fallback). Parameter regularization,
iteration count and bounds are fixed in code, not selected from trial profit.

The profile, training hash, cutoff, split and validation scores are frozen with
each NEW forecast. The next period supplies actual out-of-sample evidence.
Previously frozen probabilities and picks are never recalculated. This trains
the research challenger, not live probability calibration. Small validation
sets are noisy; passing them alone never enables publication.

## Consistent weighting and provenance

The existing opponent fit uses a 180-day half-life. The new shrunk challenger
uses the same decay in its league prior. Effective sample is bounded by both
weighted mass and Kish effective sample size. The old comparison models keep
their original priors so their accumulated results remain interpretable.

Each new context includes compact evidence snapshots: source, observation time,
goal projections, source weight, known sample counts, available historical match
identifiers/dates/scores, signatures and latest match dates. These allow overlap,
recency and rest-day audits. Stored weights describe the existing blend; snapshots
alone do not claim API independence or introduce an untrained feature bonus.

Shot-level xG is not inferred from expected_home/expected_away. The snapshot marks
it unverified until a dedicated provenance contract supplies actual shot data.
Additional verified lineups, injuries, rest and shot features require separately
versioned prospective ablation tests before influencing published probabilities.
StatsBomb's selected open competitions can support offline research, but do not
provide universal live coverage. No new paid subscription or API key is required
by this change, and no missing data is fabricated.

## Settlement, closing prices and diagnostics

The research archive supplies regulation-time scores with exact-time/team
identity checks. Missing, conflicting or ambiguous results stay pending; a later
conflict withdraws a previously counted outcome. Financial history is untouched.

Separate file .data/daily_quality/priced-totals-clv.json records each model's
entry book/side/line and later same-book quotes in the last 45 minutes before
kickoff. The existing quote-only workflow also watches these fixtures, sharing
its ten-fixture/run cap and account budgets. Missing quotes remain missing;
post-kickoff quotes never repair CLV. Multiple model picks on the same match are
correlated measurements, not independent samples or extra bankroll positions.

Reports expose paired Brier/log loss, unit P&L, ROI, sample counts and price
coverage. After ten distinct days, exploratory 95% day-block bootstrap intervals
are provided in the artifact (1000 resamples, fixed seed). They are not an
automatic significance test or proof of profitability after multiple experiments.

Persistent trial records live inside goal-history.json under priced_totals_trials.
Export: .data/exports/latest-totals-strategy-lab.json. All persistence uses existing
cache directories; Actions/cache path lists and live financial data are unchanged.

## Operating procedure

1. User runs the existing full workflow on feature/daily-quality-pipeline with
   the usual inputs. Do not run main for this experiment. Existing two-hour full
   runs and thirty-minute quote-only runs are sufficient for initial collection.
2. Inspect the FIRST run for successful critical tests, a non-error strategy
   artifact, and either new frozen fixtures or explicit skip reasons. Zero
   published forecasts remains a valid result of existing safety/quality guards.
3. After 24–48 hours inspect data/identity/settlement errors, paired coverage and
   price availability. Fix engineering faults immediately without changing
   strategy thresholds to fit those outcomes.
4. Review model calibration and strategy results weekly, keeping protocol
   parameters fixed. All model variations tried must remain visible in history.
5. First extended promotion review requires at least 200 settled picks of the
   candidate strategy across 30 distinct days. This is a review threshold, not
   sufficient evidence by itself. It can take longer than a month at low volume.
6. Promotion additionally requires a fresh untouched forward period, improvement
   versus market on paired probability scores, positive price-aware performance
   with uncertainty assessment, usable quote coverage, and robustness across
   time/league concentration. No automatic promotion and no forced daily picks.

If a challenger fails, keep it in research or retire it with its results intact.
Do not select only a profitable subgroup retrospectively and present that as
out-of-sample performance. Future changes to formulas/selection receive a new
protocol version and a separate forward cohort.

Research references consulted for this design:
* https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
* https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html
* https://escholarship.org/uc/item/4w1110bb
* https://github.com/hudl/open-data
* https://odds-api.io/blog/odds-feed-api
