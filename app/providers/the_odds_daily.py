"""Small free-credit backup: one totals market and one region per request."""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta

import httpx

from app.schemas import Offer
from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time
from app.services.fixture_identity import direct_fixture_identity
from app.utils import canonicalize_league_name

LEAGUES = {
    "soccer_epl": ("England Premier League", "English Premier League"),
    "soccer_england_championship": ("England Championship", "English League Championship"),
    "soccer_germany_bundesliga": ("Germany Bundesliga", "German Bundesliga"),
    "soccer_italy_serie_a": ("Italy Serie A", "Italian Serie A"),
    "soccer_spain_la_liga": ("Spain La Liga", "Spanish La Liga"),
    "soccer_france_ligue_one": ("France Ligue 1", "French Ligue 1"),
}


class TheOddsDailyProvider:
    gap_only = True

    def __init__(self, settings):
        self.api_key = os.getenv("THE_ODDS_API_KEY", "")
        self.used = 0

    def supports_match(self, match):
        return (
            match.sport_key == "soccer"
            and timedelta(minutes=30) <= match.commence_time - datetime.now(UTC) <= timedelta(hours=4)
            and any(
                canonicalize_league_name(match.league_name) in {canonicalize_league_name(n) for n in names}
                for names in LEAGUES.values()
            )
        )

    async def fetch_offers(self, matches):
        now = datetime.now(UTC)
        stats = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "offers_parsed": 0,
        }
        if not self.api_key:
            stats["reason"] = "missing_api_key"
            return {}, stats, {}
        targets = [m for m in matches if timedelta(minutes=30) <= m.commence_time - now <= timedelta(hours=4)]
        grouped = {}
        for match in targets:
            found = [
                key
                for key, names in LEAGUES.items()
                if canonicalize_league_name(match.league_name) in {canonicalize_league_name(name) for name in names}
            ]
            if len(found) == 1:
                grouped.setdefault(found[0], []).append(match)
        result = {}
        async with BudgetedAsyncClient(timeout=8) as client:
            # Two league calls for discovery; two more remain for exact-price
            # confirmation in this provider instance. Shared quotas still apply.
            for league, fixtures in sorted(grouped.items(), key=lambda kv: min(m.commence_time for m in kv[1]))[:2]:
                if self.used >= 4:
                    stats["budget_exhausted"] = True
                    break
                self.used += 1
                stats["requests"] += 1
                try:
                    response = await client.get(
                        "https://api.the-odds-api.com/v4/sports/" + league + "/odds",
                        params={"apiKey": self.api_key, "regions": "uk", "markets": "totals", "oddsFormat": "decimal"},
                    )
                    if response.status_code != 200:
                        stats["response_errors"] += 1
                        stats["last_status"] = response.status_code
                        break
                    events = response.json()
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    stats["response_errors"] += 1
                    stats["last_error_type"] = type(exc).__name__
                    break
                if not isinstance(events, list):
                    continue
                observed = datetime.now(UTC)
                for match in fixtures:
                    found = [
                        e
                        for e in events
                        if isinstance(e, dict)
                        and direct_fixture_identity(
                            match,
                            {
                                "home": e.get("home_team"),
                                "away": e.get("away_team"),
                                "commence_time": e.get("commence_time"),
                            },
                            tolerance_hours=0.25,
                        )
                    ]
                    if len(found) != 1 or not found[0].get("id"):
                        continue
                    event = found[0]
                    offers = []
                    for book in event.get("bookmakers") or []:
                        at = parse_time(book.get("last_update"))
                        if not at or not timedelta(0) <= observed - at <= timedelta(minutes=15):
                            continue
                        for market in book.get("markets") or []:
                            if market.get("key") != "totals":
                                continue
                            for outcome in market.get("outcomes") or []:
                                try:
                                    price, point = float(outcome["price"]), float(outcome["point"])
                                except (KeyError, TypeError, ValueError):
                                    continue
                                if (
                                    outcome.get("name") not in {"Over", "Under"}
                                    or not math.isfinite(price)
                                    or not math.isfinite(point)
                                    or not 1 < price <= 100
                                    or not 0 < point <= 15
                                ):
                                    continue
                                offers.append(
                                    Offer(
                                        "the_odds",
                                        str(book.get("title") or book.get("key")),
                                        "totals",
                                        outcome["name"],
                                        price,
                                        point,
                                        source_event_id=str(event["id"]),
                                        metadata={
                                            "fetched_at_utc": observed.isoformat(),
                                            "market_period": "full time",
                                            "provider_updated_at": at.isoformat(),
                                        },
                                    )
                                )
                    if offers:
                        result[match.match_key] = offers
                        stats["offers_parsed"] += len(offers)
        return result, stats, {}
