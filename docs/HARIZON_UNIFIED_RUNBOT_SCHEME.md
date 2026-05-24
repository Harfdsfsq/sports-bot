# HARIZON unified run-bot scheme

Executable target scheme for `run-bot`.

1. Daily inventory target: best 300 football matches for the Moscow day.
2. Inventory is cumulative: every run fills missing evidence and refreshes known line movement.
3. Primary providers first: `odds_api_io`, `bzzoiro`, `sstats`, `sportlogic`.
4. Supplemental/low-quota providers are shortlist and missing-role backfill only.
5. Publication is totals/spreads only and requires 2+ odds sources, 2+ context sources, and 2+ bookmakers.
6. Context providers cannot confirm price alone.
7. Line movement lifecycle: candidates outside the next 2-hour run window are held until a movement/prior observation exists; candidates inside the next run window may publish after normal gates.
8. Runtime writes `.data/exports/latest-harizon-unified-runtime-scheme.json` for audit/report comparison.

Implemented in `app/services/harizon_unified_scheme_runtime.py`, loaded by `RuntimePreflight` and `runtime_startup_chain`.
