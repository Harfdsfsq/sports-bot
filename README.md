# Sports bot fixed build

This archive contains a drop-in replacement for the Python rewrite.

What changed:
- fixed odds-api.io ingestion to use `/v3/events` plus `/v3/odds/multi`
- removed the broken assumption that odds-api.io event IDs are the same as The Odds API event IDs
- restored strong team normalization and fuzzy match resolution inspired by the legacy Google Apps Script
- added richer debug output in `.data/debug-last-run.json`
- focused the model on soccer first, with support for h2h, totals, spreads, dnb, double chance, btts, and team totals
- added environment variable compatibility for `THE_ODDS_API_KEY` and legacy `ODDS_API_KEY`

Recommended setup:
1. Replace the repository files with the archive contents.
2. Make sure these GitHub secrets exist: `THE_ODDS_API_KEY`, `ODDS_API_IO_KEY`, `SSTATS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Run the workflow once manually.
4. Inspect `.data/debug-last-run.json` after the run.
