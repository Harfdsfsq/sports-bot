# What to upload next time

For the fastest and most accurate analysis, upload **one** of these:

## Best option
The GitHub Actions artifact `learning-bundle-...zip`

## If you upload files manually
Upload at least:
- `.logs/debug-last-run.json`
- the latest `.logs/runs/...-run.json`
- `.data/state.json`
- `.data/exports/latest-quality-report.json`
- `.data/exports/latest-bets.json`
- `.data/exports/latest-matches.json`

## Why this matters

I do not automatically keep a hidden copy of your runtime data between chats.
But if the repository or an uploaded bundle contains the full decision trail, I can reconstruct:
- market selection logic
- candidate pruning
- quality rejections
- bankroll logic
- odds mismatches
- which improvements should be applied next
