"""One explicit startup path for the daily-quality profile."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path


def main():
    os.environ.setdefault('PUBLISH_DRY_RUN', 'true')
    for line in Path('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ[key] = value
    # Dry/live and keys are supplied by the workflow, never by this config file.
    from app.config import Settings
    from app.services.daily_quality_runner import DailyQualityRunner
    from app.services.log_redaction import _install_log_redaction
    logging.basicConfig(level=logging.INFO)
    _install_log_redaction()
    summary = asyncio.run(DailyQualityRunner(Settings(_env_file=None)).run_once())
    if summary.get('telegram_delivery_errors'):
        raise SystemExit('Some forecast deliveries failed; see current report')
    return summary


if __name__ == '__main__':
    main()
