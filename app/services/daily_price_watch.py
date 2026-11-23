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

    journal = read_json(totals_strategy_clv.PATH, {})
    experiments = [row for row in journal.values() if row["status"] == "pending"]
    experimental_targets = {(row["match_key"], parse_time(row["kickoff"])) for row in experiments}
    selected_targets = {
        (row["match_key"], parse_time(row["kickoff"])) for row in experiments if row.get("market_filter_selected")
    }
    selected_without_quote = {
        (row["match_key"], parse_time(row["kickoff"]))
        for row in experiments if row.get("market_filter_selected") and not row.get("reference_price")
    }
    all_requested = requested | experimental_targets
    totals_strategy_clv.update([], {}, now)
    targets = [
        m
        for m in registry.runtime_matches()
        if (m.match_key, m.commence_time) in all_requested
        and timedelta(0) < m.commence_time - now <= timedelta(minutes=45)
    ]
    def priority(match):
        key = (match.match_key, match.commence_time)
        return key not in requested, key not in selected_targets, key not in selected_without_quote, match.commence_time

    targets = sorted(targets, key=priority)[:10]
    if not targets:
        return {"targets": 0, "resolved": finalized.get("resolved_count", 0)}
    try:
        offers, stats = {}, {}
        pari_requested = {
            (row.get("match_key"), parse_time(row.get("commence_time")))
            for row in pending
            if str(row.get("entry_bookmaker") or "").lower() == "pari"
        }
        pari_requested.update(
            (row.get("match_key"), parse_time(row.get("kickoff")))
            for row in experiments
            if row.get("book") == "pari"
        )
        pari_targets = [m for m in targets if (m.match_key, m.commence_time) in pari_requested][:2]
        fetches = {}
        if provider:
            fetches["primary"] = provider.fetch_offers(targets)
        if pari_provider and pari_targets:
            fetches["pari"] = pari_provider.watch_offers(pari_targets)
        responses = await asyncio.gather(
            *(asyncio.wait_for(fetch, timeout=30) for fetch in fetches.values()), return_exceptions=True
        )
        for name, response in zip(fetches, responses, strict=True):
            if isinstance(response, asyncio.CancelledError):
                raise response
            if isinstance(response, BaseException):
                stats[name + "_error_type"] = type(response).__name__
                continue
            quotes_by_match, provider_stats, _ = response
            for key, quotes in quotes_by_match.items():
                offers.setdefault(key, []).extend(quotes)
            if name == "primary":
                stats.update(provider_stats)
            else:
                stats["pari_requests"] = provider_stats.get("requests", 0)
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
            "market_filter_targets": sum((m.match_key, m.commence_time) in selected_targets for m in targets),
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
