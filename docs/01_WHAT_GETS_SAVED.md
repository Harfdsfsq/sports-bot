# What gets saved

The bundle intentionally captures the files that are most useful for debugging how the bot made a decision:

- `.logs/debug-last-run.json`
- recent `.logs/runs/*/*-run.json`
- `.data/state.json`
- latest exports from `.data/exports/latest-*`
- recent quality reports
- recent daily reports
- market monitor json files, if present

This gives enough material to answer:
- why a match passed or failed
- which guards killed candidates
- what the bankroll and open exposure were
- what the quality layer changed
- what Telegram actually published
- what the recent learning state looked like
