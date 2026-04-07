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


    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": True,
            "api_key_present": bool(getattr(self.settings, "odds_api_io_key", None)),
            "event_requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "matches_built": 0,
            "low_tier_skipped": 0,
            "simulated_skipped": 0,
            "event_http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_matches": []}
        api_key = getattr(self.settings, "odds_api_io_key", None)
        if not api_key:
            return [], stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4))
        until = now + timedelta(days=days_ahead)
        timeout = float(getattr(self.settings, "odds_api_io_timeout_seconds", 25.0) or 25.0)
        max_pages = max(1, int(getattr(self.settings, "odds_api_io_max_pages_per_sport", 4) or 4))
        page_limit = max(1, int(getattr(self.settings, "odds_api_io_page_limit", 100) or 100))
        matches: list[Match] = []
        seen_ids: set[int] = set()
        async with httpx.AsyncClient(timeout=timeout) as client:
            for page in range(1, max_pages + 1):
                params = {
                    "apiKey": api_key,
                    "sport": "football",
                    "status": "pending,live",
                    "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "limit": page_limit,
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
                before = len(seen_ids)
                for raw in items:
                    event = self._parse_event(raw)
                    if event is None:
                        if isinstance(raw, dict) and is_simulated_or_esports_event(
                            str(raw.get("home") or ""),
                            str(raw.get("away") or ""),
                            str((raw.get("league") or {}).get("name") or ""),
                        ):
                            stats["simulated_skipped"] += 1
                        continue
                    event_id = int(event.get("id") or 0)
                    if event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)
                    tier = "low" if self._looks_low_tier(event.get("league") or "") else "mid"
                    if tier == "low" and not bool(getattr(self.settings, "allow_low_tier", False)):
                        stats["low_tier_skipped"] += 1
                        continue
                    match = Match(
                        source="odds_api_io",
                        source_event_id=str(event_id),
                        sport_key="soccer",
                        league_name=str(event.get("league") or ""),
                        home_team=str(event.get("home") or ""),
                        away_team=str(event.get("away") or ""),
                        commence_time=event["commence_time"],
                        home_team_norm="",
                        away_team_norm="",
                        league_key="",
                        tier=tier,
                        metadata={
                            "odds_api_io_id": event_id,
                            "competition": event.get("league"),
                            "raw_event": raw,
                        },
                    )
                    matches.append(match)
                stats["events_fetched"] = len(seen_ids)
                stats["matches_built"] = len(matches)
                if len(seen_ids) == before or len(items) < page_limit:
                    break
        preview["sample_matches"] = [
            {
                "match_key": item.match_key,
                "league_name": item.league_name,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "commence_time": item.commence_time.isoformat(),
                "tier": item.tier,
            }
            for item in matches[:5]
        ]
        return matches, stats, preview

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
            "requested_bookmakers": None,
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
        stats["requested_bookmakers"] = target_books

        events: list[dict[str, Any]] = []
        seen_event_ids: set[int] = set()
        timeout = float(getattr(self.settings, "odds_api_io_timeout_seconds", 25.0) or 25.0)
        max_pages = max(1, int(getattr(self.settings, "odds_api_io_max_pages_per_sport", 4) or 4))
        page_limit = max(1, int(getattr(self.settings, "odds_api_io_page_limit", 100) or 100))

        async with httpx.AsyncClient(timeout=timeout) as client:
            for page in range(1, max_pages + 1):
                params = {
                    "apiKey": api_key,
                    "sport": "football",
                    "status": "pending,live",
                    "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "limit": page_limit,
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
                if len(items) < page_limit:
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
        """Restrict odds-api.io requests to Bet365 and Unibet only.

        We intentionally ignore any extra bookmaker names that may appear in
        env/config so the provider cannot silently widen coverage again.
        """
        preferred = list(getattr(self.settings, "odds_api_io_bookmakers", []) or [])
        values: list[str] = []
        allowed = {
            "bet365": "Bet365",
            "unibet": "Unibet",
        }
        for item in preferred:
            raw = str(item or "").strip()
            if not raw:
                continue
            value = allowed.get(normalize_bookmaker_name(raw))
            if value and value not in values:
                values.append(value)
        return ",".join(values or ["Bet365", "Unibet"])

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

        def to_float(value: Any) -> float | None:
            try:
                if value in (None, ""):
                    return None
                return float(str(value).replace(",", "."))
            except Exception:
                return None

        def add_offer(bookmaker: str, family: str, selection: str, price_value: Any, point: Any = None, market_name: str = "") -> None:
            price = to_float(price_value)
            if price is None or price <= 1.0:
                return
            point_value = to_float(point)
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
            if not self._is_supported_market(market_key):
                return
            family = self._family_for_market(market_key)
            if family is None:
                return
            for row in outcomes:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("label") or row.get("selection") or "").strip()
                point = (
                    row.get("point")
                    or row.get("line")
                    or row.get("handicap")
                    or row.get("hdp")
                    or row.get("max")
                )
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
                add_offer(bookmaker_name, family, selection, row.get("price") or row.get("odds") or row.get("decimal") or row.get("value"), point, market_key)

        def parse_market_rows(bookmaker_name: str, market_key: str, rows: list[dict[str, Any]]) -> None:
            if not self._is_supported_market(market_key):
                return
            family = self._family_for_market(market_key)
            if family is None:
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue

                point = (
                    row.get("point")
                    or row.get("line")
                    or row.get("handicap")
                    or row.get("hdp")
                    or row.get("max")
                )
                label = str(row.get("label") or row.get("name") or row.get("selection") or "").strip()

                if family == "h2h":
                    added = False
                    for selection, field in ((match.home_team, "home"), ("Draw", "draw"), (match.away_team, "away")):
                        price = row.get(field) or row.get(f"{field}_od")
                        if price not in (None, ""):
                            add_offer(bookmaker_name, family, selection, price, None, market_key)
                            added = True
                    if not added and label:
                        selection = self._map_h2h_selection(label, match)
                        add_offer(bookmaker_name, family, selection, row.get("price") or row.get("odds") or row.get("decimal") or row.get("value"), None, market_key)
                    continue

                if family == "totals":
                    added = False
                    over_price = row.get("over") or row.get("over_od")
                    under_price = row.get("under") or row.get("under_od")
                    if over_price not in (None, ""):
                        add_offer(bookmaker_name, family, "Over", over_price, point, market_key)
                        added = True
                    if under_price not in (None, ""):
                        add_offer(bookmaker_name, family, "Under", under_price, point, market_key)
                        added = True
                    if not added and label:
                        selection = "Over" if label.lower().startswith("over") else "Under" if label.lower().startswith("under") else label
                        add_offer(bookmaker_name, family, selection, row.get("price") or row.get("odds") or row.get("decimal") or row.get("value"), point, market_key)
                    continue

                if family == "spreads":
                    added = False
                    home_price = row.get("home") or row.get("home_od")
                    away_price = row.get("away") or row.get("away_od")
                    point_value = to_float(point)
                    if home_price not in (None, ""):
                        add_offer(bookmaker_name, family, match.home_team, home_price, point_value, market_key)
                        added = True
                    if away_price not in (None, ""):
                        away_point = -point_value if point_value is not None else point
                        add_offer(bookmaker_name, family, match.away_team, away_price, away_point, market_key)
                        added = True
                    if not added and label:
                        selection = match.home_team if label.lower() in {"home", match.home_team.lower(), "1"} else match.away_team if label.lower() in {"away", match.away_team.lower(), "2"} else label
                        normalized_point = point_value
                        if selection == match.away_team and normalized_point is not None:
                            normalized_point = -normalized_point
                        add_offer(bookmaker_name, family, selection, row.get("price") or row.get("odds") or row.get("decimal") or row.get("value"), normalized_point, market_key)

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
            if isinstance(bookmaker_payload, list):
                markets = bookmaker_payload
            elif isinstance(bookmaker_payload, dict):
                markets = bookmaker_payload.get("markets", bookmaker_payload)
            else:
                continue

            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    market_key = str(market.get("key") or market.get("name") or market.get("market") or "").lower()
                    outcomes = market.get("outcomes")
                    if isinstance(outcomes, list):
                        parse_outcomes(bookmaker_name, market_key, [row for row in outcomes if isinstance(row, dict)])
                        continue
                    odds_rows = market.get("odds")
                    if isinstance(odds_rows, list):
                        parse_market_rows(bookmaker_name, market_key, [row for row in odds_rows if isinstance(row, dict)])
            elif isinstance(markets, dict):
                for market_key, market_value in markets.items():
                    key = str(market_key or "").lower()
                    if isinstance(market_value, dict):
                        outcomes = market_value.get("outcomes")
                        if isinstance(outcomes, list):
                            parse_outcomes(bookmaker_name, key, [row for row in outcomes if isinstance(row, dict)])
                            continue
                        odds_rows = market_value.get("odds")
                        if isinstance(odds_rows, list):
                            parse_market_rows(bookmaker_name, key, [row for row in odds_rows if isinstance(row, dict)])
                            continue
                    if isinstance(market_value, list):
                        rows = [row for row in market_value if isinstance(row, dict)]
                        parse_outcomes(bookmaker_name, key, rows)
                        parse_market_rows(bookmaker_name, key, rows)

        return offers

    @staticmethod
    def _is_supported_market(market_key: str) -> bool:
        key = str(market_key or '').lower().strip()
        if not key:
            return False
        banned_terms = (
            '1st half', 'first half', '2nd half', 'second half', 'half time', 'halftime', 'ht',
            'corner', 'corners', 'booking', 'bookings', 'card', 'cards', 'throw', 'throws',
            'offside', 'offsides', 'shot', 'shots', 'foul', 'fouls', 'team total', 'home total',
            'away total', 'player', 'next goal', 'correct score', 'double chance', 'draw no bet',
            'btts', 'both teams to score', 'alternative', 'alternate', 'alt ', 'race to', 'odd/even',
            'clean sheet', 'win to nil', 'to qualify', 'penalty', 'minute', 'asian corners',
        )
        if any(term in key for term in banned_terms):
            return False
        return True

    @staticmethod
    def _family_for_market(market_key: str) -> str | None:
        key = str(market_key or "").lower().strip()
        if key in {"h2h", "1x2", "moneyline", "ml", "match winner", "match result", "full time result"} or "moneyline" in key:
            return "h2h"
        if key in {"totals", "goals over/under", "goal line", "over/under", "over under", "ou", "o/u"} or key.startswith("totals ") or key.startswith("goals over/under"):
            return "totals"
        if key in {"spread", "spreads", "handicap", "asian handicap"} or key.startswith("spread ") or key.startswith("handicap "):
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


    def _looks_low_tier(self, league_name: str) -> bool:
        text = str(league_name or "").lower()
        markers = ("u17", "u18", "u19", "u20", "u21", "u23", "women", "reserve", "friendly", "esports")
        return any(marker in text for marker in markers)
