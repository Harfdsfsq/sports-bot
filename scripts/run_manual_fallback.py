"""Run the guarded reserve only after a fresh, empty primary publication pass."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.send_pipeline_run_report import fresh_summary, timestamp


def main() -> int:
    started = timestamp(os.getenv('RUN_STARTED_AT_UTC'))
    if started is None:
        print('Missing RUN_STARTED_AT_UTC; reserve blocked')
        return 1
    summary = fresh_summary(Path.cwd(), started)
    if not summary:
        print('Missing current-run summary; reserve blocked')
        return 1
    dry = os.getenv('PUBLISH_DRY_RUN', 'true').lower() in {'true', '1', 'yes', 'on'}
    if int(summary.get('published_to_telegram') or 0) > 0 or (dry and int(summary.get('candidates_publishable') or 0) > 0):
        print('Primary publication already selected forecasts; reserve skipped')
        return 0
    return subprocess.call([sys.executable, '-u', 'scripts/publish_controlled_fallback_guarded.py'])


if __name__ == '__main__':
    raise SystemExit(main())
