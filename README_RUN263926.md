# RUN263926 accumulation analytics v3

This patch fixes the remaining accumulation-stage analytics issues:

- candidate/value/runtime rows without explicit `point` now merge with fallback/API rows by extracting the line from `selection` (`Меньше 2.5`, `Over 2.5`, etc.);
- nested `metrics` blocks from fallback reports are flattened before ledger/calibration calculations;
- sparse API/value rows no longer create extra ledger rows missing team/quality/edge fields;
- current-run summary reports missing metrics only for the active run.

Publication logic is not changed.
