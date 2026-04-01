from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import (
    canonicalize_league_name,
    canonicalize_team_name,
    detect_market_family,
    get_outcome_key,
    get_spread_selection_key,
    get_total_selection_key,
    infer_team_total_side,
    parse_datetime,
    score_event_match,
)

SPORT_MAP = {
    "soccer": "football",
    "basketball": "basketball",
    "baseball": "baseball",
    "icehockey": "ice-hockey",
}


class OddsApiIoProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://api.odds-api.io/v3"

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_odds_api_io and self.settings.odds_api_io_key),
            "api_key_present": bool(self.settings.odds_api_io_key),
            "event_requests": 0,
            "odds_requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "events_matched": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_offer_events": 0,
            "markets_parsed": 0,
            "offers_parsed": 0,
            "event_http_statuses": [],
            "odds_http_statuses": [],
            "payload_shapes": [],
            "bookmakers_seen": 0,
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"unmatched_events": [], "matched_examples": [], "response_debug": []}
        if not self.settings.enable_odds_api_io or not self.settings.odds_api_io_key or not matches:
            return {}, stats, preview

        selected_matches = matches[: self.settings.max_matches_for_odds_fetch]
        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in selected_matches:
            grouped[match.sport_key].append(match)

        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        async with httpx.AsyncClient(timeout=self.settings.odds_api_io_timeout_seconds) as client:
            for sport_key, sport_matches in grouped.items():
                sport_slug = SPORT_MAP.get(sport_key)
                if not sport_slug:
                    continue
                events = await self._fetch_events_for_sport(client, sport_slug, stats)
                stats["events_fetched"] += len(events)
                mapping, unmatched = self._map_events_to_matches(events, sport_matches, stats)
                for item in unmatched[:20]:
                    preview["unmatched_events"].append(item)
                if not mapping:
                    continue
                stats["events_matched"] += len(mapping)
                for event_id, entry in list(mapping.items())[:10]:
                    preview["matched_examples"].append(
                        {
                            "event_id": event_id,
                            "mode": entry["mode"],
                            "score": round(entry["score"], 2),
                            "home": entry["event"].get("home"),
                            "away": entry["event"].get("away"),
                            "match_key": entry["match"].match_key,
                        }
                    )
                event_ids = list(mapping.keys())
                for start in range(0, len(event_ids), 10):
                    chunk = event_ids[start : start + 10]
                    rows = await self._fetch_odds_chunk(client, chunk, stats)
                    for row in rows:
                        event_id = str(row.get("id") or "")
                        entry = mapping.get(event_id)
                        if entry is None:
                            stats["unmatched_offer_events"] += 1
                            continue
                        parsed_offers, markets_parsed = self._parse_event_odds(row, entry["match"], entry["mode"])
                        stats["markets_parsed"] += markets_parsed
                        stats["offers_parsed"] += len(parsed_offers)
                        stats["bookmakers_seen"] += len(row.get("bookmakers") or []) if isinstance(row.get("bookmakers"), (dict, list)) else 0
                        preview["response_debug"].append({
                            "event_id": event_id,
                            "markets_parsed": markets_parsed,
                            "offers_parsed": len(parsed_offers),
                            "top_level_keys": sorted(list(row.keys()))[:12],
                            "bookmakers_type": type(row.get("bookmakers") or {}).__name__,
                        })
                        if parsed_offers:
                            offers_by_match[entry["match"].match_key].extend(parsed_offers)

        return {key: value for key, value in offers_by_match.items()}, stats, preview

    async def _fetch_events_for_sport(self, client: httpx.AsyncClient, sport_slug: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now(UTC) - timedelta(hours=2)
        cutoff = datetime.now(UTC) + timedelta(days=self.settings.run_days_ahead)
        skip = 0
        rows: list[dict[str, Any]] = []
        for _page in range(self.settings.odds_api_io_max_pages_per_sport):
            params = {
                "apiKey": self.settings.odds_api_io_key,
                "sport": sport_slug,
                "status": "pending",
                "from": now.isoformat().replace("+00:00", "Z"),
                "to": cutoff.isoformat().replace("+00:00", "Z"),
                "limit": self.settings.odds_api_io_page_limit,
                "skip": skip,
            }
            stats["event_requests"] += 1
            try:
                response = await client.get(f"{self.base_url}/events", params=params)
            except Exception:
                stats["response_errors"] += 1
                break
            stats.setdefault("event_http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:500]
            if response.status_code != 200:
                stats["response_errors"] += 1
                break
            try:
                payload = response.json()
            except Exception:
                stats["response_errors"] += 1
                break
            if not isinstance(payload, list):
                if isinstance(payload, dict):
                    stats.setdefault("payload_shapes", []).append(",".join(sorted(payload.keys())[:10]))
                    payload = payload.get("data") or payload.get("events") or payload.get("results") or []
                if not isinstance(payload, list):
                    break
            rows.extend([item for item in payload if isinstance(item, dict)])
            if len(payload) < self.settings.odds_api_io_page_limit:
                break
            skip += self.settings.odds_api_io_page_limit
        return rows

    def _map_events_to_matches(
        self,
        events: list[dict[str, Any]],
        matches: list[Match],
        stats: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        mapped: dict[str, dict[str, Any]] = {}
        best_for_match: dict[str, tuple[str, float]] = {}
        unmatched: list[dict[str, Any]] = []
        for event in events:
            event_home = str(event.get("home") or "").strip()
            event_away = str(event.get("away") or "").strip()
            event_league = str((event.get("league") or {}).get("name") or "").strip()
            try:
                event_start = parse_datetime(event.get("date"))
            except Exception:
                unmatched.append(
                    {
                        "event_id": event.get("id"),
                        "home": event_home,
                        "away": event_away,
                        "league": event_league,
                        "reason": "bad_date",
                    }
                )
                continue
            best_score = 0.0
            best_mode: str | None = None
            best_match: Match | None = None
            for match in matches:
                score, mode = score_event_match(
                    sport=match.sport_key,
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=event_home,
                    event_away=event_away,
                    event_start=event_start,
                    event_league=event_league,
                    exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                    fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
                )
                if score > best_score:
                    best_score = score
                    best_mode = mode
                    best_match = match
            if best_match is None or best_mode is None:
                unmatched.append(
                    {
                        "event_id": event.get("id"),
                        "home": event_home,
                        "away": event_away,
                        "league": event_league,
                        "date": event.get("date"),
                        "reason": "no_match",
                    }
                )
                continue
            previous = best_for_match.get(best_match.match_key)
            if previous is not None and previous[1] >= best_score:
                continue
            if previous is not None:
                mapped.pop(previous[0], None)
            event_id = str(event.get("id") or "")
            mapped[event_id] = {
                "match": best_match,
                "mode": best_mode,
                "score": best_score,
                "event": event,
            }
            best_for_match[best_match.match_key] = (event_id, best_score)
        for entry in mapped.values():
            mode = entry["mode"]
            if mode == "exact":
                stats["matched_exact"] += 1
            elif mode == "loose":
                stats["matched_loose"] += 1
            else:
                stats["matched_fuzzy"] += 1
        return mapped, unmatched

    async def _fetch_odds_chunk(self, client: httpx.AsyncClient, event_ids: list[str], stats: dict[str, Any]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        params = {
            "apiKey": self.settings.odds_api_io_key,
            "eventIds": ",".join(event_ids),
            "bookmakers": ",".join(self.settings.consensus_bookmakers),
        }
        stats["odds_requests"] += 1
        try:
            response = await client.get(f"{self.base_url}/odds/multi", params=params)
        except Exception:
            stats["response_errors"] += 1
            return []
        if response.status_code != 200:
            retry_params = dict(params)
            retry_params.pop("apiKey", None)
            retry_params["apikey"] = self.settings.odds_api_io_key
            try:
                response = await client.get(f"{self.base_url}/odds/multi", params=retry_params)
            except Exception:
                stats["response_errors"] += 1
                return []
            stats.setdefault("odds_http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:800]
            if response.status_code != 200:
                stats["response_errors"] += 1
                return []
        try:
            payload = response.json()
        except Exception:
            stats["response_errors"] += 1
            return []
        if isinstance(payload, list):
            stats.setdefault("payload_shapes", []).append("odds:list")
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            stats.setdefault("payload_shapes", []).append("odds:" + ",".join(sorted(payload.keys())[:10]))
            direct_rows = payload.get("data") or payload.get("results") or payload.get("events")
            if isinstance(direct_rows, list):
                return [item for item in direct_rows if isinstance(item, dict)]
            rows: list[dict[str, Any]] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    if "id" not in value:
                        value = dict(value)
                        value["id"] = key
                    rows.append(value)
            return rows
        return []

    def _parse_event_odds(self, row: dict[str, Any], match: Match, match_mode: str) -> tuple[list[Offer], int]:
        bookmakers = row.get("bookmakers") or {}
        offers: list[Offer] = []
        markets_parsed = 0
        bookmaker_rows: list[tuple[str, Any]] = []
        if isinstance(bookmakers, dict):
            bookmaker_rows.extend(bookmakers.items())
        elif isinstance(bookmakers, list):
            for bookmaker in bookmakers:
                if not isinstance(bookmaker, dict):
                    continue
                bookmaker_name = str(bookmaker.get("name") or bookmaker.get("title") or bookmaker.get("key") or "")
                bookmaker_rows.append((bookmaker_name, bookmaker))
        else:
            return [], 0
        for bookmaker_name, markets in bookmaker_rows:
            if isinstance(markets, dict):
                markets = markets.get("markets") or markets.get("odds") or markets.get("lines") or markets
                if isinstance(markets, dict):
                    markets = list(markets.values())
            if not isinstance(markets, list):
                continue
            for market in markets:
                market_name = str(market.get("name") or "")
                market_key = str(market.get("key") or market_name)
                detected = detect_market_family(market_key, market_name, match.sport_key)
                if detected is None:
                    continue
                family, subtype = detected
                prices = market.get("odds") or market.get("outcomes") or market.get("prices") or []
                if isinstance(prices, dict):
                    prices = [prices]
                if not isinstance(prices, list):
                    continue
                markets_parsed += 1
                for price_row in prices:
                    if not isinstance(price_row, dict):
                        continue
                    offers.extend(self._parse_market_row(family, subtype, bookmaker_name, market_name, market_key, price_row, match, row, match_mode))
        return offers, markets_parsed

    def _parse_market_row(
        self,
        family: str,
        subtype: str,
        bookmaker_name: str,
        market_name: str,
        market_key: str,
        price_row: dict[str, Any],
        match: Match,
        raw_row: dict[str, Any],
        match_mode: str,
    ) -> list[Offer]:
        offers: list[Offer] = []
        meta = {
            "match_mode": match_mode,
            "raw_event_id": raw_row.get("id"),
            "event_home": raw_row.get("home"),
            "event_away": raw_row.get("away"),
        }
        if family == "h2h":
            for key, selection in [("home", match.home_team), ("draw", "Draw"), ("away", match.away_team)]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="h2h",
                            selection=selection,
                            price=price,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "doubleChance":
            for key, selection in [("1x", "1X"), ("x2", "X2"), ("12", "12")]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="doubleChance",
                            selection=selection,
                            price=price,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "dnb":
            for key, selection in [("home", match.home_team), ("away", match.away_team)]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="dnb",
                            selection=selection,
                            price=price,
                            point=0.0,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "btts":
            for key, selection in [("yes", "Yes"), ("no", "No")]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="btts",
                            selection=selection,
                            price=price,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "totals":
            point = self._first_float(price_row, ["point", "line", "total", "max"])
            if point is None:
                return offers
            for key, selection in [("over", "Over"), ("under", "Under")]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="totals",
                            selection=selection,
                            price=price,
                            point=point,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "teamTotals":
            point = self._first_float(price_row, ["point", "line", "total", "max"])
            team_side = infer_team_total_side(market_name, market_key, "", match.home_team, match.away_team)
            if point is None or team_side is None:
                return offers
            for key, selection in [("over", "Over"), ("under", "Under")]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="teamTotals",
                            selection=selection,
                            price=price,
                            point=point,
                            team_side=team_side,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        if family == "spreads":
            raw_line = self._first_float(price_row, ["point", "line", "handicap", "hdp", "home_line"])
            home_line = self._first_float(price_row, ["home_line", "homeHandicap", "home_spread", "homeSpread", "hdp"])
            away_line = self._first_float(price_row, ["away_line", "awayHandicap", "away_spread", "awaySpread"])
            if home_line is None and away_line is None and raw_line is not None:
                home_line = raw_line
                away_line = -raw_line
            elif home_line is None and away_line is not None:
                home_line = -away_line
            elif away_line is None and home_line is not None:
                away_line = -home_line
            if home_line is None or away_line is None:
                return offers
            for key, selection, point in [("home", match.home_team, home_line), ("away", match.away_team, away_line)]:
                price = self._to_float(price_row.get(key))
                if price and price > 1.0:
                    offers.append(
                        Offer(
                            source="odds_api_io",
                            bookmaker=str(bookmaker_name),
                            family="spreads",
                            selection=selection,
                            price=price,
                            point=point,
                            market_name=market_name,
                            market_key=market_key,
                            market_subtype=subtype,
                            source_event_id=str(raw_row.get("id") or ""),
                            metadata=dict(meta),
                        )
                    )
            return offers
        return offers

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _first_float(self, payload: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            value = self._to_float(payload.get(key))
            if value is not None:
                return value
        return None
