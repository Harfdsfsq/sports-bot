"""Dedicated quote-only dispatch: no predictions, settlement or Telegram."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path


def main():
    for line in Path('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            if any(token in key for token in ('API_KEY', '_KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
                raise ValueError('Credentials belong in workflow secrets')
            os.environ[key] = value
    from app.config import Settings
    from app.providers.odds_api_io import OddsApiIoProvider
    from app.services.daily_match_registry import DailyMatchRegistry, write_json
    from app.services.daily_price_watch import watch_pre_kickoff_prices
    from app.services.market_monitor import MarketMonitor

    settings = Settings(_env_file=None)
    registry = DailyMatchRegistry(Path('.data/daily_quality/registry.json'), datetime.now(UTC))
    summary = asyncio.run(watch_pre_kickoff_prices(registry, MarketMonitor(settings), OddsApiIoProvider(settings)))
    summary.update(mode='price_watch_only', created_at_utc=datetime.now(UTC).isoformat())
    write_json('.data/exports/latest-price-watch.json', summary)
    if summary.get('error_type'):
        raise SystemExit('Quote check failed; see latest-price-watch.json')
    return summary


if __name__ == '__main__':
    main()
