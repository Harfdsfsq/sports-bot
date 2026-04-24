# Tiered controlled fallback fix for normal Run bot

## Why this fix exists

The latest run had real candidates, but no publication:

- candidates before quality: 5
- candidates after quality: 0
- main blockers: historical guard, post-calibration edge guard, no-bet quality score
- controlled fallback saw 5 candidates but rejected all of them

The closest viable candidate was blocked because it was `teamTotals`, had only 1 book, and the previous fallback policy allowed only `totals,dnb` with strict 2-book requirements.

## What changed

This package keeps the normal `Run bot` workflow but replaces the fallback policy with a tiered one.

### Tier A

Preferred fallback:

- 2+ bookmakers
- totals / dnb / teamTotals
- positive canonical EV
- positive canonical edge
- better quality threshold

### Tier B

Secondary 2-book fallback:

- lower odds cap
- lower but still positive EV/edge
- small stake only

### Tier C

Single-book rescue mode:

- only when there are zero normal quality picks
- only one forecast
- only small stake
- only totals / teamTotals / dnb
- requires positive canonical EV after recalculating from selected odds
- explicitly labels the Telegram message as controlled fallback / single-book risk

## Why this is safer than simply weakening quality

The main quality layer remains strict. Emergency/historical/last-resort internal publication stays disabled.

The fallback is external, explicit, low-stake, and audited. It writes:

- `artifacts/controlled-fallback-report.json`
- `.data/exports/latest-controlled-fallback-report.json`
- `.data/exports/latest-controlled-fallback-pick.json`
- updated `latest-picks.json` when it selects a pick
- `artifacts/run-bot-bundle.zip`

## How to apply

1. Unzip into the repository root.
2. Review diff in GitHub Desktop.
3. Commit and push.
4. Run normal **Run bot** with profile `balanced`.
5. Send back `run-bot-current` artifact.

## Expected result

If the next run looks like the previous one, the bot should publish one small fallback forecast instead of zero, while still rejecting negative-EV candidates.
