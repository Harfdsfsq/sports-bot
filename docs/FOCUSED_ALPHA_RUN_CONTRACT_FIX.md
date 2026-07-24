# Focused Alpha run contract repair

Production run `30074030963` selected a 94-match Focused Alpha cohort and built non-empty provider routing, but the main runner stopped before provider enrichment.

Root causes:

1. `daily_coverage_full_inventory_provider_patch` converted the native `(matches, metadata)` filter result into a list. The outer near-window wrapper expected two values and raised `ValueError: not enough values to unpack (expected 2, got 0)`.
2. The Telegram report treated all 88 historical `telegram_sent` rows from cumulative `latest-picks.json` as sends from the current run.

Repairs:

- install a contract-safe Focused Alpha filter immediately after the original provider-scope patch;
- preserve both tuple and legacy list return shapes;
- scope main-pipeline publication counts to rows timestamped after the current run anchor;
- never count cumulative pending-ledger rows as a current-run send;
- surface `.logs/debug-last-run.json:error` as `run_failed` when no current-run prediction was sent;
- run focused compile, Ruff and regression tests before merge.

No publication threshold, source-independence, xG, movement, price-integrity, bankroll or daily-cap guard is relaxed.
