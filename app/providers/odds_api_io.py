from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import (
    is_simulated_or_esports_event,
    normalize_bookmaker_name,
    parse_datetime,
    score_event_match,
)


class OddsApiIoProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://api.odds-api.io/v3"

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": True,
            "api_key_present": bool(getattr(self.settings, "odds_api_io_key", None)),
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
        preview: dict[str, Any] = {"sample_events": [], "sample_odds": []}

        api_key = getattr(self.settings, "odds_api_io_key", None)
        if not api_key:
            return {}, stats, preview

        soccer_matches = [
            match
            for match in matches
            if match.sport_key == "soccer" and not is_simulated_or_esports_event(match.home_team, match.away_team, match.league_name)
        ]
        if not soccer_matches:
            return {}, stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4))
        until = now + timedelta(days=days_ahead)
        target_books = self._bookmakers_param()

        events: list[dict[str, Any]] = []
        seen_event_ids: set[int] = set()
        async with httpx.AsyncClient(timeout=25.0) as client:
            for page in range(1, 5):
                params = {
                    "apiKey": api_key,
                    "sport": "football",
                    "status": "pending,live",
                    "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "limit": 100,
                    "page": page,
                }
                stats["event_requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}/events", params=params)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"events request failed: {exc}"
                    continue

                stats["event_http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:1500]
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue

                payload = self._safe_json(response)
                if payload is None:
                    stats["response_errors"] += 1
                    continue
                shape = self._payload_shape(payload)
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)

                items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                if page == 1 and items:
                    preview["sample_events"] = items[:3]
                if not items:
                    break

                before = len(seen_event_ids)
                for item in items:
                    event_id = item.get("id")
                    if not isinstance(event_id, int):
                        continue
                    if event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)
                    events.append(item)
                stats["events_fetched"] = len(events)
                if len(seen_event_ids) == before:
                    break
                if len(items) < 100:
                    break

            mapping: dict[str, dict[str, Any]] = {}
            for raw_event in events:
                event = self._parse_event(raw_event)
                if event is None:
                    if isinstance(raw_event, dict) and is_simulated_or_esports_event(
                        str(raw_event.get("home") or ""),
                        str(raw_event.get("away") or ""),
                        str((raw_event.get("league") or {}).get("name") or ""),
                    ):
                        stats["simulated_skipped"] += 1
                    continue
                matched = self._match_event(event, soccer_matches)
                if matched is None:
                    stats["unmatched_offer_events"] += 1
                    continue
                existing = mapping.get(matched.match_key)
                if existing is None or float(event.get("match_score") or 0.0) > float(existing["event"].get("match_score") or 0.0):
                    mapping[matched.match_key] = {"match": matched, "event": event}

            stats["events_matched"] = len(mapping)
            for item in mapping.values():
                quality = item["event"].get("match_quality")
                if quality == "exact":
                    stats["matched_exact"] += 1
                elif quality == "loose":
                    stats["matched_loose"] += 1
                elif quality == "fuzzy":
                    stats["matched_fuzzy"] += 1

            matched_items = list(mapping.values())
            matched_items.sort(key=lambda row: self._match_priority(row["match"], now))
            max_fetch = max(1, int(getattr(self.settings, "max_matches_for_odds_fetch", 40) or 40))
            if len(matched_items) > max_fetch:
                matched_items = matched_items[:max_fetch]
                stats["matches_limited_to"] = max_fetch

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            bookmakers_seen: set[str] = set()
            for start in range(0, len(matched_items), 10):
                chunk = matched_items[start : start + 10]
                if not chunk:
                    continue
                event_ids = ",".join(str(item["event"]["id"]) for item in chunk)
                params = {
                    "apiKey": api_key,
                    "eventIds": event_ids,
                    "bookmakers": target_books,
                }
                stats["odds_requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}/odds/multi", params=params)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"odds request failed: {exc}"
                    continue

                stats["odds_http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:2000]
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue

                payload = self._safe_json(response)
                if payload is None:
                    stats["response_errors"] += 1
                    continue
                shape = self._payload_shape(payload)
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)
                if len(preview["sample_odds"]) < 2:
                    preview["sample_odds"].append(payload)

                event_list = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                for event_payload in event_list:
                    event_id = int(event_payload.get("id") or 0)
                    row = next((item for item in chunk if int(item["event"]["id"]) == event_id), None)
                    if row is None:
                        continue
                    parsed = self._parse_event_odds(event_payload, row["match"])
                    if not parsed:
                        continue
                    offers_by_match[row["match"].match_key].extend(parsed)
                    stats["offers_parsed"] += len(parsed)
                    stats["markets_parsed"] += len({(offer.bookmaker, offer.family, offer.market_name, offer.point) for offer in parsed})
                    bookmakers_seen.update(offer.bookmaker for offer in parsed)

            stats["bookmakers_seen"] = len(bookmakers_seen)
            return dict(offers_by_match), stats, preview

    def _bookmakers_param(self) -> str:
        values: list[str] = []
        for item in list(getattr(self.settings, "target_bookmakers", []) or []) + list(getattr(self.settings, "consensus_bookmakers", []) or []):
            name = str(item or "").strip()
            if name and name not in values:
                values.append(name)
        return ",".join(values or ["Bet365", "Unibet", "Pinnacle", "Betfair"])

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    @staticmethod
    def _payload_shape(payload: Any) -> str:
        if isinstance(payload, list):
            return "list"
        if isinstance(payload, dict):
            return ",".join(sorted(map(str, payload.keys()))[:12])
        return type(payload).__name__

    def _parse_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        home = str(raw.get("home") or "").strip()
        away = str(raw.get("away") or "").strip()
        league = str((raw.get("league") or {}).get("name") or "").strip()
        if not home or not away:
            return None
        if is_simulated_or_esports_event(home, away, league):
            return None
        try:
            commence_time = parse_datetime(raw.get("date"))
        except Exception:
            return None
        return {
            "id": int(raw.get("id") or 0),
            "home": home,
            "away": away,
            "league": league,
            "commence_time": commence_time,
            "raw": raw,
        }

    def _match_event(self, event: dict[str, Any], matches: list[Match]) -> Match | None:
        best_match: Match | None = None
        best_score = 0.0
        best_quality: str | None = None
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        for match in matches:
            score, quality = score_event_match(
                sport=match.sport_key,
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=event["home"],
                event_away=event["away"],
                event_start=event["commence_time"],
                event_league=event["league"],
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if score > best_score:
                best_score = score
                best_quality = quality
                best_match = match
        if best_match is None or best_score < 48.0:
            return None
        event["match_score"] = best_score
        event["match_quality"] = best_quality
        return best_match

    def _match_priority(self, match: Match, now: datetime) -> tuple[int, int, int, float, str, str]:
        publish_window = now + timedelta(hours=max(1, int(getattr(self.settings, "publish_window_hours", 48) or 48)))
        in_window = 0 if now <= match.commence_time <= publish_window else 1
        tier_rank = 0 if match.tier == "top" else 1 if match.tier == "mid" else 2
        kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
        return (in_window, tier_rank, 0 if match.metadata.get("bet365_id") else 1, kickoff_distance, match.league_name.lower(), match.home_team.lower())

    def _parse_event_odds(self, payload: dict[str, Any], match: Match) -> list[Offer]:
        bookmakers = payload.get("bookmakers")
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()

        def add_offer(bookmaker: str, family: str, selection: str, price_value: Any, point: Any = None, market_name: str = "") -> None:
            try:
                price = float(price_value)
            except Exception:
                return
            if price <= 1.0:
                return
            point_value = None
            try:
                if point not in (None, ""):
                    point_value = float(str(point).replace(",", "."))
            except Exception:
                point_value = None
            book = self._canonical_bookmaker(bookmaker)
            key = (book, family, selection, point_value)
            if key in seen:
                return
            seen.add(key)
            offers.append(
                Offer(
                    source="odds_api_io",
                    bookmaker=book,
                    family=family,
                    selection=selection,
                    price=price,
                    point=point_value,
                    market_name=market_name,
                    market_key=family,
                    metadata={"odds_api_io": True},
                )
            )

        def parse_outcomes(bookmaker_name: str, market_key: str, outcomes: list[dict[str, Any]]) -> None:
            family = self._family_for_market(market_key)
            if family is None:
                return
            for row in outcomes:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("label") or row.get("selection") or "").strip()
                point = row.get("point") or row.get("line")
                if family == "h2h":
                    selection = self._map_h2h_selection(name, match)
                elif family == "totals":
                    selection = "Over" if name.lower().startswith("over") else "Under" if name.lower().startswith("under") else name
                elif family == "spreads":
                    selection = match.home_team if name.lower() in {"home", match.home_team.lower(), "1"} else match.away_team if name.lower() in {"away", match.away_team.lower(), "2"} else name
                    if selection == match.away_team and point not in (None, ""):
                        try:
                            point = -float(str(point).replace(",", "."))
                        except Exception:
                            pass
                else:
                    selection = name
                add_offer(bookmaker_name, family, selection, row.get("price") or row.get("odds") or row.get("decimal"), point, market_key)

        if isinstance(bookmakers, dict):
            iterator = bookmakers.items()
        elif isinstance(bookmakers, list):
            iterator = []
            for item in bookmakers:
                if isinstance(item, dict):
                    iterator.append((str(item.get("name") or item.get("bookmaker") or "unknown"), item))
        else:
            iterator = []

        for bookmaker_name, bookmaker_payload in iterator:
            if not isinstance(bookmaker_payload, dict):
                continue
            markets = bookmaker_payload.get("markets", bookmaker_payload)
            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    market_key = str(market.get("key") or market.get("name") or market.get("market") or "").lower()
                    outcomes = market.get("outcomes")
                    if isinstance(outcomes, list):
                        parse_outcomes(bookmaker_name, market_key, outcomes)
            elif isinstance(markets, dict):
                for market_key, market_value in markets.items():
                    key = str(market_key or "").lower()
                    if isinstance(market_value, dict):
                        outcomes = market_value.get("outcomes")
                        if isinstance(outcomes, list):
                            parse_outcomes(bookmaker_name, key, outcomes)
                            continue
                    if isinstance(market_value, list):
                        parse_outcomes(bookmaker_name, key, [row for row in market_value if isinstance(row, dict)])

        return offers

    @staticmethod
    def _family_for_market(market_key: str) -> str | None:
        key = str(market_key or "").lower()
        if key in {"h2h", "1x2", "moneyline"}:
            return "h2h"
        if "total" in key or key in {"ou", "o/u"}:
            return "totals"
        if "spread" in key or "handicap" in key:
            return "spreads"
        return None

    @staticmethod
    def _canonical_bookmaker(name: str) -> str:
        norm = normalize_bookmaker_name(name)
        if norm == "bet365":
            return "Bet365"
        if norm == "unibet":
            return "Unibet"
        if norm == "betfair":
            return "Betfair"
        if norm == "pinnacle":
            return "Pinnacle"
        return str(name or "Unknown")

    @staticmethod
    def _map_h2h_selection(raw_name: str, match: Match) -> str:
        name = str(raw_name or "").strip().lower()
        if name in {"home", "1", match.home_team.lower()}:
            return match.home_team
        if name in {"away", "2", match.away_team.lower()}:
            return match.away_team
        if name in {"draw", "x", "tie"}:
            return "Draw"
        return raw_name or ""
