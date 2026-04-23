# Start here

## Default mode
Use **core_daily** as the default production profile.

## When to switch profiles
- **core_daily**: main channel, default mode, focus on cleaner daily output.
- **balanced_growth**: use only if `core_daily` gives 2+ empty runs in a row.
- **research_shadow**: audit mode, data collection, no Telegram publication.

## Fast deployment
1. Copy these folders into the repository root.
2. Commit and push.
3. Open **Actions -> Run bot profit profile -> Run workflow**.
4. Choose `core_daily`.
5. Review `.data/exports/latest-coverage-audit.json` and `.logs/debug-last-run.json` after the run.

## Expected behavior
This package is designed to reduce bad publishes from combinations like:
- single-source + heavy-shrink
- non-core + high odds
- cup + weak xG edge

It will likely reduce the number of published bets before it improves quality. That tradeoff is intentional.
