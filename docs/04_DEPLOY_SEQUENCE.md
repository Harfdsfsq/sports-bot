# Deploy sequence

## Recommended sequence
1. Copy folders into the repo root.
2. Commit and push.
3. Run **Run bot profit profile** with `core_daily`.
4. Check artifacts:
   - `.logs/debug-last-run.json`
   - `.data/exports/latest-coverage-audit.json`
   - `.data/exports/latest-history-guard-audit.json`
5. If two consecutive core runs are empty, run `balanced_growth` once manually.
6. Use `research_shadow` only to collect evidence.

## Success criteria for the next 7-14 days
- no obviously weak “best bet” with stacked risk labels
- fewer single-source heavy-shrink publishes
- cleaner odds range
- better match between market type and xG structure
- stable exposure control

## When to edit the calibration profile
Only after enough settled samples accumulate.
Do not overfit after 3-5 bets.
