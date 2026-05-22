from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_fast_budget_writes_report(tmp_path: Path) -> None:
    script = Path('scripts/apply_fast_run_budget.py')
    assert script.exists()
    env = os.environ.copy()
    env['HARIZON_FAST_RUN'] = 'true'
    env['FAST_RUN_AUTO_DISABLE_SPORTLOGIC'] = 'false'
    proc = subprocess.run([sys.executable, str(script)], cwd=Path.cwd(), env=env, check=True, capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    assert payload['fast_run'] is True
    assert payload['overrides']['DAY_INVENTORY_MAX_MATCHES'] == '300'


def test_trim_inventory_script_noop_without_fast(tmp_path: Path) -> None:
    script = Path('scripts/trim_day_inventory_fast.py')
    assert script.exists()
    env = os.environ.copy()
    env.pop('HARIZON_FAST_RUN', None)
    subprocess.run([sys.executable, str(script)], cwd=Path.cwd(), env=env, check=True)
