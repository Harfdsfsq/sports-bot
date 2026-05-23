from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_fast_budget_preserves_balanced_market_depth() -> None:
    script = Path('scripts/apply_fast_run_budget.py')
    assert script.exists()
    env = os.environ.copy()
    env['HARIZON_FAST_RUN'] = 'true'
    env['FAST_RUN_AUTO_DISABLE_SPORTLOGIC'] = 'false'
    proc = subprocess.run([sys.executable, str(script)], cwd=Path.cwd(), env=env, check=True, capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    overrides = payload['overrides']
    assert payload['fast_run'] is True
    assert int(overrides['ODDS_API_IO_REQUEST_BUDGET_GRANTED']) >= 120
    assert int(overrides['MAX_MATCHES_FOR_ODDS_FETCH']) >= 160
    assert int(overrides['PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT']) >= 160
    assert overrides['BZZOIRO_V2_FETCH_EVENT_ODDS'] == 'true'
    assert int(overrides['BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT']) >= 60


def test_ensure_controlled_fallback_report_script_exists() -> None:
    assert Path('scripts/ensure_controlled_fallback_report.py').exists()


def test_fast_workflow_reapplies_fast_limits_after_quota() -> None:
    workflow = Path('.github/workflows/run-bot-fast.yml')
    assert workflow.exists()
    text = workflow.read_text(encoding='utf-8')
    quota_pos = text.index('scripts/apply_per_run_api_quota_contract.py')
    reapply_pos = text.index('scripts/apply_fast_run_budget.py || true', quota_pos)
    assert reapply_pos > quota_pos
    assert 'scripts/ensure_controlled_fallback_report.py || true' in text
    assert 'scripts/assert_fast_run_depth.py || true' in text
