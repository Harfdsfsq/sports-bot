# Follow-up review: 2026-04-20 run

## What improved
- The run now publishes 2 picks instead of 1.
- At least one spread market reached publication, which means spread parsing is no longer completely blocked.
- Negative expected-goals corruption is no longer the main issue in the published examples.

## Remaining critical issue
The pick

- `Los Angeles FC — San Jose Earthquakes`
- `Фора 0 — San Jose Earthquakes (0)`
- `@ 3.75`

looks structurally wrong.

### Why
For an AH(0) / Draw No Bet style market, the quoted price should always be materially **lower** than the raw away moneyline price, because a draw returns stake.

But in this run the displayed line probability is `28.5%`, which is effectively the same shape as a raw away-win price. With a non-zero draw probability, that is not consistent with a genuine zero-handicap market.

## Root cause addressed in follow-up patch
1. **Spread model was not line-aware**
   - probability for spreads was built only from xG difference
   - the actual handicap line (`0`, `-0.5`, `+1`, etc.) was ignored
   - so `0`, `-0.5`, `-1.0` could receive almost the same model probability

2. **No guard against zero-handicap misclassification**
   - if provider data maps a moneyline-like price into handicap `0`, the candidate could survive
   - this is exactly the pattern shown by `San Jose (0) @ 3.75`

## Fixes added
- `app/services/model.py`
  - spread probability is now computed with a line-aware Asian handicap settlement model from Poisson scorelines
  - zero-handicap spreads are rejected when their bookmaker price is effectively the same as the same-side `h2h` price from the same book

## Still visible in the log
The run remains skewed toward basic markets:

- `candidates_before_quality = 3`
- `published = 2`
- `publishable_with_derived_market_signal = 0`
- `missing_context_spreads = 21`
- `missing_context_dnb = 21`
- `missing_context_btts = 14`
- `publish_books_guard = 34`

So totals / BTTS / derived markets still do not reach publication often enough.
