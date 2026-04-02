from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import (
    detect_market_family,
    get_outcome_key,
    get_spread_selection_key,
    get_total_selection_key,
    infer_team_total_side,
    normalize_bookmaker_name,
    parse_datetime,
    score_event_match,
)

BOOKMAKER_ALIASES = {
    "bet365": "Bet365",
    "bet365sportsbook": "Bet365",
    "unibet": "Unibet",
    "unibetuk": "Unibet",
    "unibetfr": "Unibet",
    "unibetnl": "Unibet",
    "betfair": "Betfair",
    "betfairexchange": "Betfair",
    "pinnacle": "Pinnacle",
    "matchbook": "Matchbook",
}


class BookiesApiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (settings.bookies_api_base_url or "https://bookiesapi.com/api/get.php").rstrip("/")

    async def fetch_offers(
        self,
        matches: list[Match],
        existing_offer_maps: dict[str, dict[str, list[Offer]]] | None = None,
    ) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.bookies_api_enabled),
            "candidate_matches": 0,
            "event_requests": 0,
            "odds_requests": 0,
            "events_fetched": 0,
            "events_matched": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_offer_events": 0,
            "markets_parsed": 0,
            "offers_parsed": 0,
            "response_errors": 0,
            "event_http_statuses": [],
            "odds_http_statuses": [],
            "payload_shapes": [],
            "bookmakers_seen": 0,
            "last_body_preview": None,
            "task_used": self.settings.bookies_api_odds_task,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_odds": []}

        if not self.settings.bookies_api_enabled:
            return {}, stats, preview

        token = self.settings.bookies_api_token or self.settings.bookies_api_key
        if not self.settings.bookies_api_login or not token:
            stats["response_errors"] += 1
            stats["last_body_preview"] = "Missing BOOKIES_API_LOGIN or BOOKIES_API_TOKEN/BOOKIES_API_KEY"
            return {}, stats, preview

        candidates = self._candidate_matches(matches, existing_offer_maps or {})
        stats["candidate_matches"] = len(candidates)
        max_matches = max(1, int(self.settings.max_matches_for_odds_fetch or 300))
        if len(candidates) > max_matches:
            candidates = candidates[:max_matches]
            stats["candidate_matches_limited_to"] = max_matches
        if not candidates:
            return {}, stats, preview

        candidates_by_date = self._candidate_lookup_by_date(candidates)
        matched_events: dict[str, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self.settings.bookies_api_timeout_seconds) as client:
            for day_key, day_matches in sorted(candidates_by_date.items()):
                # ВАЖНО: BookiesAPI predatapage лучше работает с DD.MM.YYYY
                dt = datetime.fromisoformat(day_key).replace(tzinfo=UTC)
                day_candidates = [
                    dt.strftime("%d.%m.%Y"),
                    dt.strftime("%Y%m%d"),
                ]

                for page in range(1, max(1, self.settings.bookies_api_max_pages_per_day) + 1):
                    items: list[Any] = []
                    payload: Any | None = None

                    for day_value in day_candidates:
                        params = self._auth_params(
                            "predatapage",
                            {
                                "sport": self._normalize_sport_name("soccer"),
                                "day": day_value,
                                "p": page,
                            },
                        )
                        stats["event_requests"] += 1
                        response = await self._safe_get(client, params)
                        if response is None:
                            stats["response_errors"] += 1
                            continue

                        stats["event_http_statuses"].append(response.status_code)
                        stats["last_body_preview"] = response.text[:1200]

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

                        parsed_items = self._get_event_list(payload)
                        if parsed_items:
                            items = parsed_items
                            break

                    stats["events_fetched"] += len(items)

                    if page == 1 and items:
                        preview["sample_events"].extend(items[:3])

                    if not items:
                        break

                    matched_this_page = 0
                    for raw in items:
                        event = self._parse_event(raw)
                        if event is None:
                            continue

                        matched = self._match_event(event, day_matches)
                        if matched is None:
                            stats["unmatched_offer_events"] += 1
                            continue

                        matched_events[matched.match_key] = {
                            "match": matched,
                            "event": event,
                            "raw": raw,
                        }
                        matched_this_page += 1
                        stats["events_matched"] += 1

                        quality = event.get("match_quality")
                        if quality == "exact":
                            stats["matched_exact"] += 1
                        elif quality == "loose":
                            stats["matched_loose"] += 1
                        elif quality == "fuzzy":
                            stats["matched_fuzzy"] += 1

                    if len(items) < max(1, self.settings.bookies_api_page_limit):
                        break
                    if matched_this_page == 0 and page >= 2:
                        break

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            bookmakers_seen: set[str] = set()

            for match_key, item in matched_events.items():
                game_id = item["event"]["game_id"]
                parsed_offers, odds_payload = await self._fetch_odds_for_game(client, game_id, item["match"], stats)

                if odds_payload is not None and len(preview["sample_odds"]) < 2:
                    preview["sample_odds"].append(odds_payload)

                if not parsed_offers and self.settings.bookies_api_odds_task != "odds":
                    parsed_offers, odds_payload = await self._fetch_odds_for_game(
                        client,
                        game_id,
                        item["match"],
                        stats,
                        task="odds",
                    )
                    if odds_payload is not None and len(preview["sample_odds"]) < 2:
                        preview["sample_odds"].append(odds_payload)

                if not parsed_offers:
                    continue

                offers_by_match[match_key].extend(parsed_offers)
                stats["offers_parsed"] += len(parsed_offers)
                stats["markets_parsed"] += len(
                    {(o.bookmaker, o.family, o.market_name, o.point) for o in parsed_offers}
                )
                bookmakers_seen.update(o.bookmaker for o in parsed_offers)

            stats["bookmakers_seen"] = len(bookmakers_seen)
            return dict(offers_by_match), stats, preview

    def _auth_params(self, task: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "login": self.settings.bookies_api_login or "",
            "token": self.settings.bookies_api_token or self.settings.bookies_api_key or "",
            "task": task,
            **(extra or {}),
        }

    async def _safe_get(self, client: httpx.AsyncClient, params: dict[str, Any]) -> httpx.Response | None:
        try:
            return await client.get(self.base_url, params=params)
        except Exception:
            return None

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
            return ",".join(sorted(payload.keys())[:12])
        return type(payload).__name__

    @staticmethod
    def _normalize_sport_name(sport: str) -> str:
        raw = str(sport or "").lower().strip()
        if raw == "icehockey":
            return "hockey"
        return raw or "soccer"

    def _candidate_matches(
        self,
        matches: list[Match],
        existing_offer_maps: dict[str, dict[str, list[Offer]]],
    ) -> list[Match]:
        allowed_sports = {sport.lower() for sport in (self.settings.bookies_api_sports or ["soccer"])}
        out: list[Match] = []
        for match in matches:
            if match.sport_key != "soccer":
                continue
            if allowed_sports and "soccer" not in allowed_sports:
                continue
            merged_existing: list[Offer] = []
            for source_map in existing_offer_maps.values():
                merged_existing.extend(source_map.get(match.match_key) or [])
            has_h2h = any(offer.family == "h2h" for offer in merged_existing)
            if self.settings.bookies_api_use_for_backfill_only and has_h2h:
                continue
            out.append(match)
        return out

    @staticmethod
    def _candidate_lookup_by_date(matches: list[Match]) -> dict[str, list[Match]]:
        out: dict[str, list[Match]] = defaultdict(list)
        for match in matches:
            base = match.commence_time.astimezone(UTC).date()
            for offset in (-1, 0, 1):
                out[(base + timedelta(days=offset)).isoformat()].append(match)
        return out

    def _get_event_list(self, payload: Any) -> list[Any]:
        if payload is None:
            return []

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ("games_pre", "data", "results", "response", "games", "matches", "events", "fixtures"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value

            seen: set[str] = set()
            out: list[Any] = []

            def walk(node: Any, depth: int = 0) -> None:
                if depth > 7 or node is None:
                    return
                if isinstance(node, list):
                    for item in node:
                        walk(item, depth + 1)
                    return
                if not isinstance(node, dict):
                    return

                parsed = self._parse_event(node)
                if parsed is not None:
                    key = parsed["game_id"] or f"{parsed['home']}|{parsed['away']}|{parsed['iso_date']}"
                    if key not in seen:
                        seen.add(key)
                        out.append(node)

                for value in node.values():
                    if isinstance(value, (list, dict)):
                        walk(value, depth + 1)

            walk(payload)
            return out

        return []

    def _parse_event(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None

        def pick_team(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, (str, int, float)):
                return str(value).strip()
            if isinstance(value, dict):
                for key in ("name", "team_name", "teamName", "title", "label", "full_name", "fullName"):
                    if value.get(key):
                        return str(value[key]).strip()
            return ""

        home = (
            pick_team(raw.get("home"))
            or pick_team(raw.get("home_team"))
            or pick_team(raw.get("team1"))
            or pick_team(raw.get("localteam"))
            or pick_team(raw.get("homeTeam"))
            or pick_team(raw.get("ht"))
        )
        away = (
            pick_team(raw.get("away"))
            or pick_team(raw.get("away_team"))
            or pick_team(raw.get("team2"))
            or pick_team(raw.get("visitorteam"))
            or pick_team(raw.get("awayTeam"))
            or pick_team(raw.get("at"))
        )

        if not home or not away:
            teams = raw.get("teams")
            if isinstance(teams, list) and len(teams) >= 2:
                home = home or pick_team(teams[0])
                away = away or pick_team(teams[1])

        if not home or not away:
            # ВАЖНО: структура BookiesAPI predatapage
            home = home or pick_team(raw.get("home"))
            away = away or pick_team(raw.get("away"))

        if not home or not away:
            return None

        game_id = self._first_text(raw, ["game_id", "gameId", "id", "match_id", "event_id"])
        league = self._extract_league(raw)

        date_value = None
        for key in ("commence_time", "start", "starts_at", "start_time", "time", "date", "datetime", "isoDate"):
            if raw.get(key) not in (None, ""):
                date_value = raw.get(key)
                break

        if date_value is None and isinstance(raw.get("match_time"), dict):
            date_value = raw["match_time"].get("date") or raw["match_time"].get("time")

        try:
            commence_time = parse_datetime(self._parse_date_value(date_value))
        except Exception:
            commence_time = datetime.now(UTC)

        return {
            "game_id": str(game_id or "").strip(),
            "home": str(home).strip(),
            "away": str(away).strip(),
            "league": str(league or "").strip(),
            "commence_time": commence_time,
            "iso_date": commence_time.isoformat(),
            "raw": raw,
        }

    def _extract_league(self, raw: dict[str, Any]) -> str:
        league = raw.get("league")
        if isinstance(league, dict):
            for key in ("name", "title", "league_name"):
                if league.get(key):
                    return str(league[key]).strip()

        for key in ("league_name", "competition", "competition_name", "tournament", "championship", "sport_title"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value).strip()

        return ""

    def _match_event(self, event: dict[str, Any], candidates: list[Match]) -> Match | None:
        best_match: Match | None = None
        best_score = 0.0
        best_quality: str | None = None

        for match in candidates:
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
                exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
            )
            if score > best_score:
                best_match = match
                best_score = score
                best_quality = quality

        if best_match is None:
            return None
        if best_score < 48.0:
            return None

        event["match_quality"] = best_quality
        return best_match

    async def _fetch_odds_for_game(
        self,
        client: httpx.AsyncClient,
        game_id: str,
        match: Match,
        stats: dict[str, Any],
        task: str | None = None,
    ) -> tuple[list[Offer], Any | None]:
        if not game_id:
            return [], None

        params = self._auth_params(task or self.settings.bookies_api_odds_task, {"game_id": game_id})
        stats["odds_requests"] += 1

        response = await self._safe_get(client, params)
        if response is None:
            stats["response_errors"] += 1
            return [], None

        stats["odds_http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1200]

        if response.status_code != 200:
            stats["response_errors"] += 1
            return [], None

        payload = self._safe_json(response)
        if payload is None:
            stats["response_errors"] += 1
            return [], None

        shape = self._payload_shape(payload)
        if shape not in stats["payload_shapes"]:
            stats["payload_shapes"].append(shape)

        debug_meta: dict[str, Any] = {
            "shape": shape,
            "type": type(payload).__name__,
            "task": task or self.settings.bookies_api_odds_task,
            "game_id": str(game_id),
            "match": {
                "home": match.home_team,
                "away": match.away_team,
                "commence_time": match.commence_time.isoformat(),
            },
        }

        if isinstance(payload, dict):
            debug_meta["top_keys"] = list(payload.keys())[:20]
            for key in ("data", "results", "response", "bookmakers", "odds", "markets", "values", "games_pre"):
                value = payload.get(key)
                if isinstance(value, list):
                    debug_meta[f"{key}_len"] = len(value)
                    if value and isinstance(value[0], dict):
                        debug_meta[f"{key}_item0_keys"] = list(value[0].keys())[:20]
                elif isinstance(value, dict):
                    debug_meta[f"{key}_keys"] = list(value.keys())[:20]
                elif value is not None:
                    debug_meta[f"{key}_type"] = type(value).__name__

        stats["last_body_preview"] = {
            "meta": debug_meta,
            "body_preview": response.text[:2000],
        }

        parsed = self._parse_odds_payload(payload, match)
        if not parsed:
            stats["response_errors"] += 1
        return parsed, payload

    def _parse_odds_payload(self, payload: Any, match: Match) -> list[Offer]:
        if isinstance(payload, dict) and "games_pre" in payload:
            return []

        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None, str | None]] = set()
        known_books = self._known_bookmakers()

        def add_offer(
            bookmaker: str,
            family: str,
            selection: str,
            price: float,
            *,
            point: float | None = None,
            market_name: str = "",
            market_key: str = "",
            team_side: str | None = None,
        ) -> None:
            book = self._canonical_bookmaker_name(bookmaker)
            if not book or price <= 1.0:
                return

            key = (book, family, selection, point, team_side)
            if key in seen:
                return
            seen.add(key)

            offers.append(
                Offer(
                    source="bookies_api",
                    bookmaker=book,
                    family=family,  # type: ignore[arg-type]
                    selection=selection,
                    price=price,
                    point=point,
                    team_side=team_side,
                    market_name=market_name,
                    market_key=market_key,
                    market_subtype="bookies_api",
                    metadata={"bookies_api": True},
                )
            )

        def parse_selection_item(item: dict[str, Any], ctx: dict[str, Any]) -> None:
            bookmaker = ctx.get("bookmaker") or self._first_text(item, ["bookmaker", "bookie", "company", "site", "source"])
            market_key = str(ctx.get("market_key") or item.get("market_key") or item.get("key") or "")
            market_name = str(ctx.get("market_name") or item.get("market_name") or item.get("market") or item.get("type") or item.get("name") or "")
            descriptor = " > ".join(part for part in [ctx.get("path"), market_name] if part)

            info = detect_market_family(market_key, descriptor or market_name, match.sport_key)
            if info is None:
                return
            family, _ = info

            price = self._to_float(self._first_value(item, ["price", "odd", "odds", "value", "decimal", "koef"]))
            if price is None or price <= 1.0:
                return

            point = self._to_float(self._first_value(item, ["point", "line", "total", "handicap", "hdp"]))
            if point is None:
                point = self._to_float(ctx.get("point"))

            raw_name = self._first_text(item, ["name", "label", "selection", "outcome", "team", "title", "type"])

            if family == "h2h":
                selection = get_outcome_key(raw_name, match.home_team, match.away_team) or raw_name
                if selection in {"home", "away", "draw"}:
                    selection = {"home": match.home_team, "away": match.away_team, "draw": "Draw"}[selection]
                add_offer(bookmaker, "h2h", selection, price, point=point, market_name=market_name, market_key=market_key)

            elif family == "totals":
                total_key = get_total_selection_key(raw_name)
                if total_key and point is not None:
                    add_offer(
                        bookmaker,
                        "totals",
                        "Over" if total_key == "over" else "Under",
                        price,
                        point=point,
                        market_name=market_name,
                        market_key=market_key,
                    )

            elif family == "spreads":
                side = get_spread_selection_key(raw_name, match.home_team, match.away_team)
                if side and point is not None:
                    selection = match.home_team if side == "home" else match.away_team if side == "away" else raw_name
                    add_offer(bookmaker, "spreads", selection, price, point=point, market_name=market_name, market_key=market_key)

            elif family == "dnb":
                side = get_outcome_key(raw_name, match.home_team, match.away_team)
                if side in {"home", "away"}:
                    selection = match.home_team if side == "home" else match.away_team
                    add_offer(bookmaker, "dnb", selection, price, market_name=market_name, market_key=market_key)

            elif family == "doubleChance":
                raw = raw_name.strip().lower().replace(" ", "")
                mapping = {
                    "1x": "1X",
                    "x2": "X2",
                    "12": "12",
                    "homedraw": "1X",
                    "awaydraw": "X2",
                    "homeaway": "12",
                }
                if raw in mapping:
                    add_offer(bookmaker, "doubleChance", mapping[raw], price, market_name=market_name, market_key=market_key)

            elif family == "btts":
                raw = raw_name.strip().lower()
                if raw in {"yes", "both teams to score - yes", "btts yes"}:
                    add_offer(bookmaker, "btts", "Yes", price, market_name=market_name, market_key=market_key)
                elif raw in {"no", "both teams to score - no", "btts no"}:
                    add_offer(bookmaker, "btts", "No", price, market_name=market_name, market_key=market_key)

            elif family == "teamTotals":
                total_key = get_total_selection_key(raw_name)
                side = infer_team_total_side(market_name, market_key, raw_name, match.home_team, match.away_team)
                if total_key and side and point is not None:
                    add_offer(
                        bookmaker,
                        "teamTotals",
                        "Over" if total_key == "over" else "Under",
                        price,
                        point=point,
                        market_name=market_name,
                        market_key=market_key,
                        team_side=side,
                    )

        def parse_scalar_mapping(node: dict[str, Any], ctx: dict[str, Any]) -> None:
            market_key = str(ctx.get("market_key") or "")
            market_name = str(ctx.get("market_name") or "")
            descriptor = " > ".join(part for part in [ctx.get("path"), market_name] if part)

            info = detect_market_family(market_key, descriptor or market_name, match.sport_key)
            if info is None:
                return

            bookmaker = ctx.get("bookmaker")
            point = self._to_float(ctx.get("point"))

            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    continue
                price = self._to_float(value)
                if price is None or price <= 1.0:
                    continue
                parse_selection_item(
                    {"name": key, "price": price},
                    {
                        **ctx,
                        "point": point,
                        "market_name": market_name,
                        "market_key": market_key,
                        "bookmaker": bookmaker,
                    },
                )

        def walk(node: Any, ctx: dict[str, Any] | None = None, depth: int = 0, path: list[str] | None = None) -> None:
            if depth > 8 or node is None:
                return

            ctx = dict(ctx or {})
            path = list(path or [])

            if isinstance(node, list):
                for item in node:
                    if isinstance(item, dict):
                        parse_selection_item(item, {**ctx, "path": " > ".join(path)})
                    walk(item, ctx, depth + 1, path)
                return

            if not isinstance(node, dict):
                return

            direct_book = self._first_text(node, ["bookmaker", "bookie", "company", "site", "source"])
            if direct_book:
                ctx["bookmaker"] = direct_book

            if not ctx.get("market_key"):
                mk = self._first_text(node, ["market_key", "key"])
                if mk:
                    ctx["market_key"] = mk

            if not ctx.get("market_name"):
                mn = self._first_text(node, ["market_name", "market", "type", "group", "bet", "wager"])
                if mn:
                    ctx["market_name"] = mn

            direct_point = self._to_float(self._first_value(node, ["point", "line", "total", "handicap", "hdp"]))
            if direct_point is not None:
                ctx["point"] = direct_point

            if any(not isinstance(v, (dict, list)) for v in node.values()):
                parse_scalar_mapping(node, {**ctx, "path": " > ".join(path)})

            for key, value in node.items():
                child = dict(ctx)
                norm_key = normalize_bookmaker_name(key)
                if norm_key in known_books:
                    child["bookmaker"] = known_books[norm_key]
                if self._looks_like_market_code(key):
                    child["market_key"] = key
                    child["market_name"] = key
                numeric_key = self._to_float(key)
                if numeric_key is not None and child.get("market_key"):
                    child["point"] = numeric_key
                walk(value, child, depth + 1, path + [str(key)])

        walk(payload)
        return offers

    def _known_bookmakers(self) -> dict[str, str]:
        out = dict(BOOKMAKER_ALIASES)
        for name in [*self.settings.target_bookmakers, *self.settings.consensus_bookmakers]:
            out[normalize_bookmaker_name(name)] = name
        return out

    @staticmethod
    def _first_text(node: Any, keys: list[str]) -> str:
        if not isinstance(node, dict):
            return ""
        for key in keys:
            value = node.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @staticmethod
    def _first_value(node: Any, keys: list[str]) -> Any:
        if not isinstance(node, dict):
            return None
        for key in keys:
            if key in node and node.get(key) not in (None, ""):
                return node.get(key)
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _looks_like_market_code(key: str) -> bool:
        raw = str(key or "").lower()
        return any(part in raw for part in ["h2h", "money", "spread", "handicap", "total", "btts", "draw", "chance"])

    def _canonical_bookmaker_name(self, bookmaker: str) -> str:
        raw = str(bookmaker or "").strip()
        if not raw:
            return ""
        normalized = normalize_bookmaker_name(raw)
        return self._known_bookmakers().get(normalized, BOOKMAKER_ALIASES.get(normalized, raw))

    @staticmethod
    def _parse_date_value(value: Any) -> str:
        if value is None or value == "":
            return datetime.now(UTC).isoformat()

        if isinstance(value, (int, float)):
            number = int(value)
            if number > 1_000_000_000:
                if number < 1_000_000_000_000:
                    return datetime.fromtimestamp(number, tz=UTC).isoformat()
                return datetime.fromtimestamp(number / 1000, tz=UTC).isoformat()
            return str(value)

        raw = str(value).strip()

        if raw.isdigit() and len(raw) in {10, 13}:
            number = int(raw)
            if len(raw) == 10:
                return datetime.fromtimestamp(number, tz=UTC).isoformat()
            return datetime.fromtimestamp(number / 1000, tz=UTC).isoformat()

        # DD.MM.YYYY [HH:MM[:SS]]
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC).isoformat()
            except Exception:
                pass

        raw = raw.replace("/", "-")
        if " " in raw and "T" not in raw:
            raw = raw.replace(" ", "T", 1)
        return raw
