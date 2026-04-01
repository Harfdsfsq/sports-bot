from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings, THE_ODDS_SPORTS_DEFAULT
from app.schemas import Match, Offer
from app.utils import (
    canonicalize_league_name,
    canonicalize_team_name,
    detect_market_family,
    get_total_selection_key,
    is_low_tier_league,
    normalize_text,
    parse_datetime,
)


class TheOddsApiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://api.the-odds-api.com/v4"

    async def fetch(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.the_odds_api_key),
            "api_key_present": bool(self.settings.the_odds_api_key),
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "matches_kept": 0,
            "markets_parsed": 0,
            "offers_parsed": 0,
            "quota_remaining": None,
            "quota_used": None,
        }
        if not self.settings.the_odds_api_key:
            return {"matches": [], "offers_by_match": {}, "stats": stats, "preview": {}}

        matches: list[Match] = []
        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        now = datetime.now(UTC)
        cutoff = now + timedelta(days=self.settings.run_days_ahead)
        league_keys = self._league_keys_to_fetch()

        async with httpx.AsyncClient(timeout=self.settings.the_odds_timeout_seconds) as client:
            for league_key in league_keys:
                sport_key = self._sport_from_league_key(league_key)
                if sport_key not in self.settings.run_sports:
                    continue
                params = {
                    "apiKey": self.settings.the_odds_api_key,
                    "regions": ",".join(self.settings.the_odds_regions),
                    "markets": "h2h,spreads,totals",
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                }
                stats["requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}/sports/{league_key}/odds/", params=params)
                except Exception:
                    stats["response_errors"] += 1
                    continue
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue
                stats["quota_remaining"] = response.headers.get("x-requests-remaining")
                stats["quota_used"] = response.headers.get("x-requests-used")
                try:
                    payload = response.json()
                except Exception:
                    stats["response_errors"] += 1
                    continue
                if not isinstance(payload, list):
                    continue
                stats["events_fetched"] += len(payload)
                for item in payload:
                    try:
                        commence = parse_datetime(item.get("commence_time"))
                    except Exception:
                        continue
                    if commence < now or commence > cutoff:
                        continue
                    league_name = str(item.get("sport_title") or league_key)
                    if not self.settings.allow_low_tier and is_low_tier_league(league_name):
                        continue
                    match = Match(
                        source="the_odds_api",
                        source_event_id=str(item.get("id") or ""),
                        sport_key=sport_key,
                        league_name=league_name,
                        home_team=str(item.get("home_team") or "").strip(),
                        away_team=str(item.get("away_team") or "").strip(),
                        commence_time=commence,
                        home_team_norm=canonicalize_team_name(str(item.get("home_team") or "")),
                        away_team_norm=canonicalize_team_name(str(item.get("away_team") or "")),
                        league_key=canonicalize_league_name(league_name),
                        tier="low" if is_low_tier_league(league_name) else "top",
                        metadata={"league_key": league_key, "sport_key_raw": str(item.get("sport_key") or "")},
                    )
                    matches.append(match)
                    parsed_offers, markets_parsed = self._parse_bookmakers(item.get("bookmakers"), match)
                    stats["markets_parsed"] += markets_parsed
                    stats["offers_parsed"] += len(parsed_offers)
                    if parsed_offers:
                        offers_by_match[match.match_key].extend(parsed_offers)
                stats["matches_kept"] = len(matches)

        preview = {
            "sample_matches": [
                {
                    "sport": match.sport_key,
                    "league": match.league_name,
                    "home": match.home_team,
                    "away": match.away_team,
                    "commence_time": match.commence_time.isoformat(),
                }
                for match in matches[:10]
            ]
        }
        return {
            "matches": matches,
            "offers_by_match": {key: value for key, value in offers_by_match.items()},
            "stats": stats,
            "preview": preview,
        }

    def _league_keys_to_fetch(self) -> list[str]:
        if self.settings.the_odds_sport_keys:
            return self.settings.the_odds_sport_keys
        keys: list[str] = []
        for sport in self.settings.run_sports:
            keys.extend(THE_ODDS_SPORTS_DEFAULT.get(sport, []))
        return keys

    @staticmethod
    def _sport_from_league_key(league_key: str) -> str:
        raw = league_key.lower().strip()
        if raw.startswith("soccer_"):
            return "soccer"
        if raw.startswith("basketball_"):
            return "basketball"
        if raw.startswith("baseball_"):
            return "baseball"
        if raw.startswith("icehockey_") or raw.startswith("hockey_"):
            return "icehockey"
        return "soccer"

    def _parse_bookmakers(self, bookmakers: Any, match: Match) -> tuple[list[Offer], int]:
        if not isinstance(bookmakers, list):
            return [], 0
        offers: list[Offer] = []
        markets_parsed = 0
        for bookmaker in bookmakers:
            book_name = str(bookmaker.get("title") or bookmaker.get("name") or bookmaker.get("key") or "Unknown")
            for market in bookmaker.get("markets") or []:
                info = detect_market_family(str(market.get("key") or ""), str(market.get("key") or ""), match.sport_key)
                if info is None:
                    continue
                family, subtype = info
                outcomes = market.get("outcomes") or []
                markets_parsed += 1
                if family == "h2h":
                    for outcome in outcomes:
                        price = self._to_float(outcome.get("price"))
                        if not price or price <= 1.0:
                            continue
                        offers.append(
                            Offer(
                                source="the_odds_api",
                                bookmaker=book_name,
                                family="h2h",
                                selection=str(outcome.get("name") or "").strip(),
                                price=price,
                                point=self._to_float(outcome.get("point")),
                                market_name=str(market.get("key") or ""),
                                market_key=str(market.get("key") or ""),
                                market_subtype=subtype,
                                source_event_id=match.source_event_id,
                                metadata={"match_mode": "native"},
                            )
                        )
                    continue
                if family == "totals":
                    for outcome in outcomes:
                        price = self._to_float(outcome.get("price"))
                        point = self._to_float(outcome.get("point"))
                        selection_key = get_total_selection_key(str(outcome.get("name") or ""))
                        if not price or price <= 1.0 or point is None or selection_key is None:
                            continue
                        offers.append(
                            Offer(
                                source="the_odds_api",
                                bookmaker=book_name,
                                family="totals",
                                selection="Over" if selection_key == "over" else "Under",
                                price=price,
                                point=point,
                                market_name="totals",
                                market_key="totals",
                                market_subtype=subtype,
                                source_event_id=match.source_event_id,
                                metadata={"match_mode": "native"},
                            )
                        )
                    continue
                if family == "spreads":
                    for outcome in outcomes:
                        price = self._to_float(outcome.get("price"))
                        point = self._to_float(outcome.get("point"))
                        if not price or price <= 1.0 or point is None:
                            continue
                        selection = str(outcome.get("name") or "").strip()
                        if not selection:
                            continue
                        offers.append(
                            Offer(
                                source="the_odds_api",
                                bookmaker=book_name,
                                family="spreads",
                                selection=selection,
                                price=price,
                                point=point,
                                market_name="spreads",
                                market_key="spreads",
                                market_subtype=subtype,
                                source_event_id=match.source_event_id,
                                metadata={"match_mode": "native"},
                            )
                        )
        return offers, markets_parsed

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None
