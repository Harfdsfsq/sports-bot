"""Read-only quote collection; never imports publication or bankroll services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.daily_match_registry import parse_time, read_json


async def watch_pre_kickoff_prices(registry, monitor, provider, *, pari_provider=None):
    """Collect same-book closing references within 45 minutes of kickoff."""
    if not monitor or not (provider or pari_provider):
        return {"targets": 0}
    now = datetime.now(UTC)
    finalized = monitor._update_pending_clv(now, {"created_at": now.isoformat(), "matches": {}, "meta": {}})
    pending = read_json(monitor.pending_clv_path, [])
    requested = {
        (row.get("match_key"), parse_time(row.get("commence_time"))) for row in pending if isinstance(row, dict)
    }
    from app.services import totals_strategy_clv

    requested.update(totals_strategy_clv.requested())
    totals_strategy_clv.update([], {}, now)
    targets = [
        m
        for m in registry.runtime_matches()
        if (m.match_key, m.commence_time) in requested and timedelta(0) < m.commence_time - now <= timedelta(minutes=45)
    ]
    targets = sorted(targets, key=lambda m: m.commence_time)[:10]
    if not targets:
        return {"targets": 0, "resolved": finalized.get("resolved_count", 0)}
    try:
        offers, stats = {}, {}
        if provider:
            try:
                offers, stats, _ = await asyncio.wait_for(provider.fetch_offers(targets), timeout=30)
            except Exception as exc:
                stats["primary_error_type"] = type(exc).__name__
        pari_requested = {
            (row.get("match_key"), parse_time(row.get("commence_time")))
            for row in pending
            if str(row.get("entry_bookmaker") or "").lower() == "pari"
        }
        pari_requested.update(
            (row.get("match_key"), parse_time(row.get("kickoff")))
            for row in read_json(totals_strategy_clv.PATH, {}).values()
            if row.get("status") == "pending" and row.get("book") == "pari"
        )
        pari_targets = [m for m in targets if (m.match_key, m.commence_time) in pari_requested][:2]
        if pari_provider and pari_targets:
            try:
                extra, pari_stats, _ = await asyncio.wait_for(pari_provider.watch_offers(pari_targets), timeout=30)
                for key, quotes in extra.items():
                    offers.setdefault(key, []).extend(quotes)
                stats["pari_requests"] = pari_stats.get("requests", 0)
            except Exception as exc:
                stats["pari_error_type"] = type(exc).__name__
        observed = datetime.now(UTC)
        offers = {
            key: [
                o
                for o in quotes
                if parse_time(o.metadata.get("fetched_at_utc"))
                and -timedelta(seconds=5)
                <= observed - parse_time(o.metadata["fetched_at_utc"])
                <= timedelta(seconds=60)
            ]
            for key, quotes in offers.items()
        }
        offers = {key: quotes for key, quotes in offers.items() if quotes}
        snapshot = monitor._build_snapshot(targets, offers, observed)
        measured = monitor._update_pending_clv(observed, snapshot)
        experiment = totals_strategy_clv.update(targets, offers, observed)
        return {
            "targets": len(targets),
            "matches_with_quotes": len(offers),
            "priced_totals_clv": experiment,
            "requests": stats.get("odds_requests", 0),
            "resolved": measured.get("resolved_count", 0) + finalized.get("resolved_count", 0),
            "pari_requests": stats.get("pari_requests", 0),
            "provider_errors": {k: v for k, v in stats.items() if k.endswith("error_type")},
            "rate_limited": bool(stats.get("any_account_rate_limited") or stats.get("rate_limited")),
            "budget_exhausted": bool(stats.get("budget_exhausted")),
        }
    except Exception as exc:
        return {"targets": len(targets), "error_type": type(exc).__name__}
