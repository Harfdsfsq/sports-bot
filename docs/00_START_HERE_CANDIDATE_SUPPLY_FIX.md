# Candidate Supply Fix for normal `Run bot`

## Problem seen in the latest run

The bot behaved safely, but the fallback had only 4 candidates to evaluate.

The no-pick report showed that candidates were rejected because:
- canonical EV was negative or too low,
- canonical edge was negative or too low,
- one previously-used candidate was correctly blocked as a duplicate.

That means the next fix should not force negative-EV picks. It should expand candidate supply before fallback.

## What this patch changes

1. Keeps internal emergency / historical / last-resort publishing disabled.
2. Re-enables market-derived candidate generation with stricter derived-signal thresholds.
3. Increases per-run and per-match pre-quality candidate pool.
4. Upgrades fallback into Tier A / Tier B / Tier C:
   - Tier A: clean 2-book reserve;
   - Tier B: softer 2-book reserve;
   - Tier C: single-book or borderline reserve, minimum stake only.
5. Keeps dedupe protection through `.data/fallback-sent-index.json`.
6. Still refuses negative canonical EV.

## How to apply

1. Unzip into the repository root.
2. Review diff in GitHub Desktop.
3. Commit and push.
4. Run normal **Run bot** with profile `balanced`.
5. Upload `run-bot-current` after the next run.

## Expected behavior

The bot should have more than 4 reserve candidates available. If any candidate has positive canonical EV and passes a tier, it will publish one controlled forecast. If all candidates are negative EV, it will still correctly publish no-pick.
