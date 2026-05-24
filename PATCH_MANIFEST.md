# HARIZON run 263719 follow-up fix

## Why
Run `26371965654` was safe (the only raw candidate became negative after quality calibration), but two infrastructure issues remained:

1. `controlled_fallback_prepublish_guard.py` blocked the no-pick diagnostic Telegram message as if it was a betting pick, because no selected candidate had 2 odds sources.
2. `repair_inventory_source_counts.py` did not consume runtime audit samples from `latest-api-coverage-consensus-runtime-patch.json` and `latest-quality-consensus-safe-relief.json`, so coverage truth could show `0` two-source/context rows while the model/quality layer had evaluated a candidate with those counts.

## Changed files
- `scripts/controlled_fallback_prepublish_guard.py`
- `scripts/repair_inventory_source_counts.py`
- `tests/test_no_pick_guard_and_evidence_repair.py`

## Safety
Publication gates are not loosened. The guard still blocks real pick messages with fewer than 2 odds sources / 2 context sources / insufficient quality. Only no-pick diagnostic reports are allowed through.

## Verification
```bash
python -m py_compile scripts/controlled_fallback_prepublish_guard.py scripts/repair_inventory_source_counts.py
PYTHONPATH=. python -m pytest -q tests/test_no_pick_guard_and_evidence_repair.py
```
