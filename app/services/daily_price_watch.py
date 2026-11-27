"""Read-only quote collection; never imports publication or bankroll services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.daily_match_registry import parse_time, read_json
from app.services.totals_strategy_lab import PAPER_BOOKS, paired_lines
from app.utils import normalize_bookmaker_name

LINE_PROVIDERS = ("odds_api_io", "sstats_pari", "the_odds")


def fresh_totals25_books(registry, match):
    offers = [quote for name in LINE_PROVIDERS for quote in (registry.cached(name, "offers", match) or [])
              if quote.point == 2.5]
    return {
        normalize_bookmaker_name(book["book"])
        for line in paired_lines(offers, registry.now, PAPER_BOOKS)
        for book in line["books"]
        if abs(parse_time(book["quotes"]["over"]["observed_at"]) -
               parse_time(book["quotes"]["under"]["observed_at"])) <= timedelta(seconds=60)
    }


async def refresh_totals25_gaps(registry, provider, *, pari_provider=None):
    """Collect fresh prices only. Success receipts feed the next normal run."""
    registry.now = datetime.now(UTC)
    candidates = []
    for match in registry.runtime_matches():
        if (timedelta(minutes=30) <= match.commence_time - registry.now <= timedelta(minutes=90)
                and registry.has_role(match, "context")):
            count = len(fresh_totals25_books(registry, match))
            if count < 3:
                candidates.append((count, match))
    candidates.sort(key=lambda item: (item[0] != 2, item[1].commence_time, item[1].match_key))
    targets = [match for _, match in candidates[:8]]
    report = {"eligible": len(candidates), "targets": len(targets), "matches_with_three_books": 0, "providers": {}}
    fetches = {}
    if provider and targets:
        fetches["odds_api_io"] = (provider, targets)
    # Use the verified IDs first so the bounded PARI batch can fill the third book.
    pari_targets = [m for m in targets if (m.metadata.get("provider_source_ids") or {}).get("sstats_pari")][:2]
    if pari_provider and pari_targets:
        fetches["sstats_pari"] = (pari_provider, pari_targets)
    results = await asyncio.gather(*(
        asyncio.wait_for(source.fetch_offers(matches), timeout=30) for source, matches in fetches.values()
    ), return_exceptions=True)
    registry.now = datetime.now(UTC)
    for (name, (_, matches)), result in zip(fetches.items(), results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            report["providers"][name] = {"targets": len(matches), "error_type": type(result).__name__}
            continue
        offers, stats, _ = result
        received = {
            m.match_key: [quote for quote in offers.get(m.match_key, [])
                          if (at := parse_time(quote.metadata.get("fetched_at_utc")))
                          and timedelta(0) <= registry.now - at <= timedelta(minutes=2)]
            for m in matches if m.commence_time > registry.now
        }
        success = [m for m in matches if received.get(m.match_key)]
        # A quote-only miss must not delay the full run's normal retry queue.
        if success:
            registry.record(name, "offers", success, received, stats, observed_at=registry.now,
                            refresh_keys={m.match_key for m in success})
        report["providers"][name] = {
            "targets": len(matches),
            "refreshed": stats.get("offer_refresh", {}).get("refreshed", 0),
            "requests": stats.get("requests", stats.get("odds_requests", 0) + stats.get("event_requests", 0)),
            "errors": stats.get("response_errors", 0),
            "budget_exhausted": bool(stats.get("budget_exhausted")),
            "rate_limited": bool(stats.get("any_account_rate_limited") or stats.get("rate_limited")),
        }
    report["matches_with_three_books"] = sum(len(fresh_totals25_books(registry, m)) >= 3 for m in targets)
    return report


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
