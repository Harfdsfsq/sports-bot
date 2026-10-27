"""Read-only quote collection; never imports publication or bankroll services."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from app.services.daily_match_registry import parse_time, read_json


async def watch_pre_kickoff_prices(registry, monitor, provider):
    """Collect same-book closing references within 45 minutes of kickoff."""
    if not monitor or not provider:
        return {'targets': 0}
    now = datetime.now(UTC)
    pending = read_json(monitor.pending_clv_path, [])
    requested = {(row.get('match_key'), parse_time(row.get('commence_time')))
                 for row in pending if isinstance(row, dict)}
    targets = [m for m in registry.matches()
               if (m.match_key, m.commence_time) in requested
               and timedelta(0) < m.commence_time - now <= timedelta(minutes=45)]
    targets = sorted(targets, key=lambda m: m.commence_time)[:10]
    if not targets:
        return {'targets': 0}
    try:
        offers, stats, _ = await asyncio.wait_for(provider.fetch_offers(targets), timeout=45)
        observed = datetime.now(UTC)
        for values in offers.values():
            for offer in values:
                offer.metadata['fetched_at_utc'] = observed.isoformat()
        snapshot = monitor._build_snapshot(targets, offers, observed)
        measured = monitor._update_pending_clv(observed, snapshot)
        return {'targets': len(targets), 'matches_with_quotes': len(offers),
                'requests': stats.get('odds_requests', 0), 'resolved': measured.get('resolved_count', 0),
                'rate_limited': bool(stats.get('any_account_rate_limited') or stats.get('rate_limited')),
                'budget_exhausted': bool(stats.get('budget_exhausted'))}
    except Exception as exc:
        return {'targets': len(targets), 'error_type': type(exc).__name__}
