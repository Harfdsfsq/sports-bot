from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer


SPORT_MAP = {
    "soccer": "football",
    "basketball": "basketball",
    "baseball": "baseball",
    "icehockey": "ice-hockey",
}


class OddsApiIoProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = "https://api.odds-api.io/v3"

    async def fetch_offers(self, matches: list[Match]):
        # Runner в этом репо ожидает: (offers_by_match, stats, preview)
        if not self.settings.odds_api_io_key:
            empty_stats = {
                "enabled": False,
                "api_key_present": False,
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
                "simulated_skipped": 0,
            }
            return {}, empty_stats, {"sample_events": []}

        max_matches = getattr(self.settings, "max_matches_for_pricing", None)
        limited_matches = matches[:max_matches] if isinstance(max_matches, int) and max_matches > 0 else matches

        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in limited_matches:
            grouped[match.sport_key].append(match)

        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        stats: dict[str, Any] = {
            "enabled": True,
            "api_key_present": True,
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
            "simulated_skipped": 0,
        }
        preview: dict[str, Any] = {"sample_events": []}

        async with httpx.AsyncClient(timeout=30.0) as client:
            for sport_key, sport_matches in grouped.items():
                ids = [
                    str(m.source_event_id)
                    for m in sport_matches
                    if getattr(m, "source", None) == "the_odds_api" and getattr(m, "source_event_id", None)
                ]
                if not ids:
                    continue

                for chunk_start in range(0, len(ids), 10):
                    chunk = ids[chunk_start : chunk_start + 10]
                    stats["odds_requests"] += 1

                    response = await client.get(
                        f"{self.base_url}/odds/multi",
                        params={
                            "apikey": self.settings.odds_api_io_key,
                            "eventIds": ",".join(chunk),
                            "bookmakers": "Bet365,Unibet",
                        },
                    )
                    stats["odds_http_statuses"].append(response.status_code)

                    if response.status_code != 200:
                        stats["response_errors"] += 1
                        continue

                    try:
                        payload = response.json()
                    except Exception:
                        stats["response_errors"] += 1
                        continue

                    stats["payload_shapes"].append(type(payload).__name__)
                    stats["last_body_preview"] = response.text[:2000]

                    # Новый формат odds-api.io: список событий
                    if isinstance(payload, list):
                        events = payload
                    # fallback на dict-формат
                    elif isinstance(payload, dict):
                        events = []
                        for event_id, event in payload.items():
                            if isinstance(event, dict):
                                item = dict(event)
                                item.setdefault("id", event_id)
                                events.append(item)
                    else:
                        events = []

                    stats["events_fetched"] += len(events)

                    for event in events:
                        event_id = str(event.get("id", ""))
                        match = next((m for m in sport_matches if str(getattr(m, "source_event_id", "")) == event_id), None)
                        if not match:
                            stats["unmatched_offer_events"] += 1
                            continue

                        stats["events_matched"] += 1

                        if len(preview["sample_events"]) < 5:
                            preview["sample_events"].append(
                                {
                                    "id": event_id,
                                    "home": event.get("home"),
                                    "away": event.get("away"),
                                    "date": event.get("date"),
                                }
                            )

                        bookmakers = event.get("bookmakers") or {}

                        if isinstance(bookmakers, dict):
                            bookmaker_items = bookmakers.items()
                        elif isinstance(bookmakers, list):
                            bookmaker_items = []
                            for book in bookmakers:
                                if isinstance(book, dict):
                                    bookmaker_items.append((book.get("name", "unknown"), book.get("markets") or []))
                        else:
                            bookmaker_items = []

                        for book_name, markets in bookmaker_items:
                            stats["bookmakers_seen"] += 1

                            if not isinstance(markets, list):
                                continue

                            for market in markets:
                                if not isinstance(market, dict):
                                    continue

                                market_name = str(market.get("name") or "").strip().lower()
                                odds_rows = market.get("odds") or []

                                family = None
                                if market_name in {"ml", "match winner", "1x2", "half time result"}:
                                    family = "h2h"
                                elif "total" in market_name or "goal" in market_name:
                                    family = "totals"
                                elif "spread" in market_name or "handicap" in market_name:
                                    family = "spreads"
                                elif market_name in {"draw no bet", "double chance", "both teams to score"}:
                                    continue

                                if family is None:
                                    continue

                                stats["markets_parsed"] += 1

                                for row in odds_rows:
                                    if not isinstance(row, dict):
                                        continue

                                    for offer in self._parse_market_row(
                                        family=family,
                                        row=row,
                                        source="odds_api_io",
                                        bookmaker=str(book_name),
                                        source_event_id=event_id,
                                    ):
                                        offers_by_match[match.match_key].append(offer)
                                        stats["offers_parsed"] += 1

        return offers_by_match, stats, preview

    def _parse_market_row(
        self,
        *,
        family: str,
        row: dict[str, Any],
        source: str,
        bookmaker: str,
        source_event_id: str,
    ) -> list[Offer]:
        offers: list[Offer] = []

        def add(selection: str, price: Any, point: Any = None, team_side: str | None = None):
            try:
                price_value = float(price)
            except (TypeError, ValueError):
                return

            if price_value <= 1.01:
                return

            point_value = None
            if point is not None:
                try:
                    point_value = float(point)
                except (TypeError, ValueError):
                    point_value = None

            offers.append(
                Offer(
                    source=source,
                    bookmaker=bookmaker,
                    family=family,
                    selection=selection,
                    price=price_value,
                    point=point_value,
                    team_side=team_side,
                    source_event_id=source_event_id,
                )
            )

        if family == "h2h":
            if "home" in row or "draw" in row or "away" in row:
                add("home", row.get("home"), team_side="home")
                add("draw", row.get("draw"))
                add("away", row.get("away"), team_side="away")
            elif "label" in row and "under" in row:
                label = str(row.get("label", "")).strip().lower()
                if "draw" in label or label == "x":
                    add("draw", row.get("under"))

        elif family == "totals":
            point = row.get("hdp") or row.get("point") or row.get("handicap")
            add("over", row.get("over"), point=point)
            add("under", row.get("under"), point=point)

        elif family == "spreads":
            point = row.get("hdp") or row.get("point") or row.get("handicap")
            add("home", row.get("home"), point=point, team_side="home")
            add("away", row.get("away"), point=point, team_side="away")

        return offers\n