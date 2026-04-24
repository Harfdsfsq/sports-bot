# Tier B near-miss controlled fallback fix

## Why this patch exists

The latest Run bot report showed that the pipeline is safe but slightly too strict:

- fallback candidates checked: 2
- no negative-EV candidate was published
- the best candidate was `Kolos Kovalivka — SC Poltava / Under 2.5`
- it had:
  - books: 2
  - confidence: 71.783
  - quality: 63.806
  - canonical edge: +1.548 pp
  - canonical EV: +3.219%
  - quality stop: `bad_historical_segment_guard`

It missed Tier B only because:
- `CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP=1.7`
- `CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT=2.8`

## What changed

Only the balanced profile is changed:

```env
CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP=1.4
CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT=3.0
```

Everything else stays protected:

- internal emergency / historical relief / last-resort are still disabled
- negative canonical EV is still hard-rejected
- Tier B still requires 2 bookmakers
- dedupe protection remains active
- Tier C remains a lower-stake backup tier

## Expected result

A candidate like the latest `Kolos — Poltava / Under 2.5` should pass as Tier B, while the negative DNB candidate remains rejected.

## How to apply

1. Unzip this archive into the repository root.
2. Check diff in GitHub Desktop.
3. Commit and push.
4. Run ordinary `Run bot` with profile `balanced`.
5. Upload `run-bot-current` after the run.
