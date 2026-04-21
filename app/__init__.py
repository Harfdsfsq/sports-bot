from __future__ import annotations

import importlib

for _name in [
    'runtime_bot_fix',
    'runtime_extra_hotfix',
    'runtime_consolidated_fix',
    'runtime_stage_next_fix',
    'runtime_run_analysis_fix',
    'runtime_current_cycle_fix',
    'runtime_books_relief_fix',
    'runtime_publish_books_stage2_fix',
    'runtime_directional_guard_fix',
    'runtime_publish_books_stage3_fix',
    'runtime_daily_risk_fix',
]:
    try:
        importlib.import_module(f'{__name__}.{_name}')
    except Exception:
        continue
