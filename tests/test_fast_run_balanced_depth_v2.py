
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_fast_workflow_keeps_app_run_mode_normal() -> None:
    workflow = Path('.github/workflows/run-bot-fast.yml').read_text(encoding='utf-8')
    assert 'RUN_MODE: "normal"' in workflow
    assert 'FAST_WORKFLOW_MODE:' in workflow
    assert 'RUN_MODE: ${{ github.event.inputs.mode' not in workflow


def test_fast_budget_uses_harizon_fast_not_app_run_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'scripts').mkdir()
    src = Path(__file__).resolve().parents[1] / 'scripts' / 'apply_fast_run_budget.py'
    dst = tmp_path / 'scripts' / 'apply_fast_run_budget.py'
    dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('RUN_MODE', 'normal')
    monkeypatch.setenv('HARIZON_FAST_RUN', 'true')
    monkeypatch.setenv('FAST_WORKFLOW_MODE', 'fast')
    result = subprocess.run([sys.executable, str(dst)], check=True, text=True, capture_output=True)
    text = result.stdout
    assert '"fast_run": true' in text
    report = (tmp_path / '.data' / 'exports' / 'latest-fast-run-budget.json').read_text(encoding='utf-8')
    assert '"RUN_MODE"' not in report  # script must not overwrite app RUN_MODE
    assert '"ODDS_API_IO_MAX_REQUESTS_PER_RUN": "160"' in report
    assert '"MAX_MATCHES_FOR_ODDS_FETCH": "220"' in report
