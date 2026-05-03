from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import is_simulated_or_esports_event, normalize_bookmaker_name, parse_datetime, score_event_match

UTC = timezone.utc


class SharpApiOddsProvider:
    """Best-effort independent odds provider for SharpAPI-compatible football feeds.

    The provider is intentionally schema-tolerant because SharpAPI/RapidAPI products expose
    slightly different JSON shapes depending on the subscribed endpoint. It is safe by default:
    it only emits mainstream soccer markets and rejects corners/cards/half-time/player markets.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(os.getenv("SHARPAPI_BASE_URL") or "https://sharpapi.p.rapidapi.com").rstrip("/")
        self.api_key = str(
            os.getenv("SHARPAPI_API_KEY")
            or os.getenv("SHARPAPI_KEY")
            or os.getenv("SHARP_API_KEY")
            or os.getenv("RAPIDAPI_KEY")
            or ""
        ).strip()
        self.rapidapi_host = str(os.getenv("SHARPAPI_RAPIDAPI_HOST") or "sharpapi.p.rapidapi.com").strip()
        self.timeout_seconds = float(os.getenv("SHARPAPI_TIMEOUT_SECONDS") or 18.0)
        self.max_http_requests = max(0, int(float(os.getenv("SHARPAPI_PER_RUN_MAX") or os.getenv("SHARPAPI_MAX_HTTP_REQUESTS_PER_RUN") or 4)))
        self.match_limit = max(1, int(float(os.getenv("SHARPAPI_MATCH_LIMIT") or 80)))
        self._requests_used = 0

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self._enabled(),
            "provider": "sharpapi",
            "api_key_present": bool(self.api_key),
            "base_url": self.base_url,
            "requests": 0,
            "response_errors": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "events_fetched": 0,
            "events_matched": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_offer_events": 0,
            "markets_parsed": 0,
            "offers_parsed": 0,
            "bookmakers_seen": 0,
            "matches_with_2plus_books": 0,
            "matches_with_1_book": 0,
            "offers_by_family": {},
            "last_body_preview": None,
            "rate_limited": False,
            "budget_exhausted": False,
            "schema_mode": "generic_json_probe",
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_odds": []}
        if not stats["enabled"] or not self.api_key:
            return {}, stats, preview

        soccer_matches = [
            match for match in matches[: self.match_limit]
            if match.sport_key == "soccer" and not is_simulated_or_esports_event(match.home_team, match.away_team, match.league_name)
        ]
        if not soccer_matches:
            return {}, stats, preview

        now = datetime.now(UTC)
        until = now + timedelta(days=max(1, int(getattr(self.settings, "run_days_ahead", 2) or 2)))
        endpoints = self._candidate_endpoints(now, until)
        events: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self._headers()) as client:
            for endpoint, params in endpoints:
                if not self._budget_allows(stats):
                    break
                self._requests_used += 1
                stats["requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}{endpoint}", params=params)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"sharpapi request failed: {exc}"
                    continue
                stats["http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:2000]
                if response.status_code == 429:
                    stats["response_errors"] += 1
                    stats["rate_limited"] = True
                    break
                if response.status_code in {401, 403}:
                    stats["response_errors"] += 1
                    stats["auth_error"] = True
                    break
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
                extracted = self._extract_event_rows(payload)
                if extracted:
                    events.extend(extracted)
                    if not preview["sample_events"]:
                        preview["sample_events"] = extracted[:3]
                    # Stop after the first endpoint that returns event-like rows.
                    break

        stats["events_fetched"] = len(events)
        if not events:
            return {}, stats, preview

        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        match_quality_counts: dict[str, int] = defaultdict(int)
        for raw_event in events:
            parsed_event = self._parse_event(raw_event)
            if parsed_event is None:
                continue
            matched = self._match_event(parsed_event, soccer_matches)
            if matched is None:
                stats["unmatched_offer_events"] += 1
                continue
            quality = str(parsed_event.get("match_quality") or "unknown")
            match_quality_counts[quality] += 1
            event_offers = self._parse_event_offers(raw_event, matched, str(parsed_event.get("id") or ""))
            if not event_offers:
                continue
            offers_by_match[matched.match_key].extend(event_offers)
            if not preview["sample_odds"]:
                preview["sample_odds"] = [self._offer_preview(item) for item in event_offers[:8]]

        stats["events_matched"] = len(offers_by_match)
        stats["matched_exact"] = int(match_quality_counts.get("exact") or 0)
        stats["matched_loose"] = int(match_quality_counts.get("loose") or 0)
        stats["matched_fuzzy"] = int(match_quality_counts.get("fuzzy") or 0)
        bookmakers_seen = {offer.bookmaker for offers in offers_by_match.values() for offer in offers}
        stats["bookmakers_seen"] = len(bookmakers_seen)
        stats["bookmakers_seen_names"] = sorted(bookmakers_seen)
        stats["offers_parsed"] = sum(len(offers) for offers in offers_by_match.values())
        stats["markets_parsed"] = len({(offer.bookmaker, offer.family, offer.market_name, offer.point) for offers in offers_by_match.values() for offer in offers})
        stats["matches_with_2plus_books"] = sum(1 for offers in offers_by_match.values() if len({offer.bookmaker for offer in offers}) >= 2)
        stats["matches_with_1_book"] = sum(1 for offers in offers_by_match.values() if len({offer.bookmaker for offer in offers}) == 1)
        family_counts: dict[str, int] = defaultdict(int)
        for offers in offers_by_match.values():
            for offer in offers:
                family_counts[str(offer.family)] += 1
        stats["offers_by_family"] = dict(sorted(family_counts.items()))
        return dict(offers_by_match), stats, preview

    def _enabled(self) -> bool:
        raw = os.getenv("ENABLE_SHARPAPI", os.getenv("SHARPAPI_ENABLED", "false"))
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if "rapidapi" in self.base_url.lower() or self.rapidapi_host:
            headers["X-RapidAPI-Key"] = self.api_key
            headers["X-RapidAPI-Host"] = self.rapidapi_host
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["X-API-Key"] = self.api_key
        return headers

    def _candidate_endpoints(self, now: datetime, until: datetime) -> list[tuple[str, dict[str, Any]]]:
        custom = [item.strip() for item in str(os.getenv("SHARPAPI_ODDS_ENDPOINTS") or "").split(",") if item.strip()]
        endpoints = custom or [
            "/odds/football",
            "/football/odds",
            "/sports/football/odds",
            "/soccer/odds",
            "/odds/soccer",
            "/events/football/odds",
        ]
        params = {
            "sport": "football",
            "league": "",
            "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "dateFrom": now.date().isoformat(),
            "dateTo": until.date().isoformat(),
            "limit": str(self.match_limit),
        }
        return [(endpoint if endpoint.startswith("/") else f"/{endpoint}", params) for endpoint in endpoints]

    def _budget_allows(self, stats: dict[str, Any]) -> bool:
        if self.max_http_requests <= 0 or self._requests_used >= self.max_http_requests:
            stats["budget_exhausted"] = True
            return False
        return True

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

    def _extract_event_rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("data", "events", "matches", "fixtures", "games", "results", "odds"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = self._extract_event_rows(value)
                if nested:
                    return nested
        return []

    def _parse_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        home = self._first_text(raw, "home", "home_team", "homeTeam", "team_home", "home_name", "localteam")
        away = self._first_text(raw, "away", "away_team", "awayTeam", "team_away", "away_name", "visitorteam")
        if not home and isinstance(raw.get("teams"), dict):
            home = self._first_text(raw["teams"], "home", "home_team", "localteam")
            away = self._first_text(raw["teams"], "away", "away_team", "visitorteam")
        league = self._league_name(raw)
        if not home or not away or is_simulated_or_esports_event(home, away, league):
            return None
        raw_time = self._first_value(raw, "commence_time", "start_time", "startTime", "date", "datetime", "kickoff", "event_time")
        try:
            commence_time = parse_datetime(raw_time)
        except Exception:
            return None
        return {
            "id": str(self._first_value(raw, "id", "event_id", "fixture_id", "game_id", "match_id") or ""),
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
                exact_tolerance_hours=float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
                fuzzy_tolerance_hours=float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8),
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

    def _parse_event_offers(self, raw: dict[str, Any], match: Match, source_event_id: str) -> list[Offer]:
        rows = self._extract_market_rows(raw)
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None, str | None]] = set()
        for market in rows:
            market_name = self._market_name(market)
            family = self._family_for_market(market_name)
            if family is None:
                continue
            bookmaker = self._bookmaker_name(market)
            outcomes = self._extract_outcome_rows(market)
            for outcome in outcomes:
                parsed = self._parse_outcome(family, market_name, outcome, match)
                if parsed is None:
                    continue
                selection, price, point, team_side = parsed
                if not self._is_offer_safe(family, selection, price, point, market_name):
                    continue
                key = (bookmaker, family, selection, point, team_side)
                if key in seen:
                    continue
                seen.add(key)
                offers.append(
                    Offer(
                        source="sharpapi",
                        bookmaker=bookmaker,
                        family=family,  # type: ignore[arg-type]
                        selection=selection,
                        price=float(price),
                        point=point,
                        team_side=team_side,
                        market_name=market_name,
                        market_key=family,
                        source_event_id=source_event_id or None,
                        metadata={
                            "sharpapi": True,
                            "raw_market_name": market_name,
                            "market_integrity": "safe_match_market",
                        },
                    )
                )
        return offers

    def _extract_market_rows(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in ("bookmakers", "sportsbooks", "odds", "markets"):
            value = raw.get(key)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    nested = item.get("markets") or item.get("odds") or item.get("outcomes")
                    if isinstance(nested, list) and any(isinstance(x, dict) for x in nested):
                        for market in nested:
                            if isinstance(market, dict):
                                merged = dict(market)
                                merged.setdefault("bookmaker", self._bookmaker_name(item))
                                rows.append(merged)
                    else:
                        rows.append(item)
            elif isinstance(value, dict):
                for bookmaker, market_list in value.items():
                    if isinstance(market_list, list):
                        for market in market_list:
                            if isinstance(market, dict):
                                merged = dict(market)
                                merged.setdefault("bookmaker", str(bookmaker))
                                rows.append(merged)
        return rows

    def _extract_outcome_rows(self, market: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("outcomes", "odds", "prices", "selections"):
            value = market.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # Some feeds put prices directly on market rows.
        direct: list[dict[str, Any]] = []
        if any(key in market for key in ("over", "under")):
            if market.get("over") not in (None, ""):
                direct.append({"name": "Over", "price": market.get("over"), "point": self._line_value(market)})
            if market.get("under") not in (None, ""):
                direct.append({"name": "Under", "price": market.get("under"), "point": self._line_value(market)})
        if any(key in market for key in ("home", "draw", "away")):
            if market.get("home") not in (None, ""):
                direct.append({"name": "home", "price": market.get("home")})
            if market.get("draw") not in (None, ""):
                direct.append({"name": "draw", "price": market.get("draw")})
            if market.get("away") not in (None, ""):
                direct.append({"name": "away", "price": market.get("away")})
        return direct

    def _parse_outcome(self, family: str, market_name: str, row: dict[str, Any], match: Match) -> tuple[str, float, float | None, str | None] | None:
        name = self._first_text(row, "name", "label", "selection", "side", "outcome", "type")
        price = self._float(self._first_value(row, "price", "odds", "decimal", "value", "american_decimal"))
        if price is None or price <= 1.0:
            return None
        point = self._float(self._first_value(row, "point", "line", "handicap", "hdp", "total", "goals"))
        if point is None:
            point = self._line_value(row)
        if point is None:
            point = self._line_value({"market_name": market_name})
        low = name.lower()
        if family == "totals":
            if "over" in low or low in {"o", "больше"}:
                return "Over", price, point, None
            if "under" in low or low in {"u", "меньше"}:
                return "Under", price, point, None
            return None
        if family == "dnb":
            if low in {"home", "1"} or match.home_team.lower() in low:
                return f"{match.home_team} (0)", price, None, "home"
            if low in {"away", "2"} or match.away_team.lower() in low:
                return f"{match.away_team} (0)", price, None, "away"
            return None
        if family == "h2h":
            if low in {"home", "1"} or match.home_team.lower() in low:
                return match.home_team, price, None, "home"
            if low in {"away", "2"} or match.away_team.lower() in low:
                return match.away_team, price, None, "away"
            if "draw" in low or low in {"x", "ничья"}:
                return "Draw", price, None, None
            return None
        return None

    @staticmethod
    def _family_for_market(name: str) -> str | None:
        text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
        if not text:
            return None
        blocked = {"corner", "corners", "card", "cards", "booking", "bookings", "player", "shot", "shots", "offsides", "throw", "throwins"}
        if any(token in text for token in blocked):
            return None
        if any(token in text for token in (" ht", "1st half", "first half", "half time", "halftime", "period")):
            return None
        if text in {"totals", "total", "total goals", "goals over under", "over under", "match goals", "asian total", "asian totals"}:
            return "totals"
        if "goals over under" in text and "team" not in text:
            return "totals"
        if text in {"draw no bet", "dnb"}:
            return "dnb"
        if text in {"ml", "moneyline", "match winner", "full time result", "1x2", "h2h"}:
            return "h2h"
        return None

    def _is_offer_safe(self, family: str, selection: str, price: float, point: float | None, market_name: str) -> bool:
        if self._family_for_market(market_name) != family:
            return False
        if family == "totals":
            if point is None or point <= 0:
                return False
            if abs(float(point) - 1.5) < 1e-9 and selection.lower().startswith("over"):
                max_price = float(os.getenv("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS") or 1.65)
                if float(price) > max_price:
                    return False
        return True

    @staticmethod
    def _first_value(row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                return row.get(key)
        return None

    def _first_text(self, row: dict[str, Any], *keys: str) -> str:
        value = self._first_value(row, *keys)
        if isinstance(value, dict):
            for nested_key in ("name", "displayName", "title"):
                if value.get(nested_key):
                    return str(value.get(nested_key)).strip()
            return ""
        return str(value or "").strip()

    def _league_name(self, row: dict[str, Any]) -> str:
        value = self._first_value(row, "league", "competition", "tournament")
        if isinstance(value, dict):
            return self._first_text(value, "name", "displayName", "title")
        return str(value or "").strip()

    def _bookmaker_name(self, row: dict[str, Any]) -> str:
        raw = self._first_value(row, "bookmaker", "sportsbook", "book", "site", "provider")
        if isinstance(raw, dict):
            raw = self._first_text(raw, "name", "title", "key")
        text = str(raw or "SharpAPI").strip()
        aliases = {
            "bet365": "Bet365",
            "unibet": "Unibet",
            "pinnacle": "Pinnacle",
            "betfair": "Betfair Exchange",
            "sbobet": "Sbobet",
        }
        return aliases.get(normalize_bookmaker_name(text), text or "SharpAPI")

    def _market_name(self, row: dict[str, Any]) -> str:
        return self._first_text(row, "market_name", "marketName", "market", "name", "key", "type")

    def _line_value(self, row: dict[str, Any]) -> float | None:
        value = self._first_value(row, "point", "line", "handicap", "hdp", "total", "goals")
        if value is not None:
            return self._float(value)
        text = " ".join(str(row.get(key) or "") for key in ("market_name", "marketName", "market", "name", "label"))
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        return self._float(match.group(1)) if match else None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    @staticmethod
    def _offer_preview(offer: Offer) -> dict[str, Any]:
        return {
            "source": offer.source,
            "bookmaker": offer.bookmaker,
            "family": offer.family,
            "selection": offer.selection,
            "point": offer.point,
            "price": offer.price,
            "market_name": offer.market_name,
        }
