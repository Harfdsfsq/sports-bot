# GitHub Actions quick start

1. Copy all folders from this archive into the repository root.
2. In GitHub, open **Settings → Secrets and variables → Actions**.
3. Make sure these repository secrets exist:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `ODDS_API_IO_KEY`
   - `ODDSPAPI_API_KEY`
   - `ALLSPORTSAPI_API_KEY`
   - `FUTRIXMETRICS_API_KEY`
   - `SSTATS_API_KEY`
   - `BZZOIRO_API_KEY`
   - `API_FOOTBALL_KEY`
   - `FOOTBALL_DATA_API_KEY`
   - `THESPORTSDB_API_KEY`
   - `NEWSAPI_KEY`
   - `GNEWS_KEY`
4. Push to `main`.
5. Open **Actions → Run bot → Run workflow**.
6. For the first manual run choose:
   - `profile = balanced`
   - `dry_run = false`
   - `reset_state = false`
   - `late_manual_mode = true`
   - `publish_window_hours = 12`
   - `max_picks_per_run = 2`

## What this package adds

- a manual/scheduled `run-bot` workflow
- profile loading from `config/balanced_output.env` or `config/conservative_passability.env`
- automatic audit payload generation after each run
- artifact upload for `.data`, `.logs`, exports, audits, and latest summaries
- a separate manual `ops-audit` workflow

## Safe first profile

Use `balanced` first. It is better aligned with the latest run logs where the main bottlenecks were:

- `market derived signal guard`
- `publish books guard`
- `quality: historical guard`

## Important note

This package is designed as a repo overlay. It does not create GitHub secrets for you. The workflows run immediately only if the required secrets are already configured in the repository.
