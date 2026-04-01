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

SPORT_MAP = {
    "soccer": "soccer",
    "basketball": "basketball",
    "baseball": "baseball",
    "icehockey": "hockey",
}

KNOWN_BOOKMAKERS = {
    "bet365": "Bet365",
    "unibet": "Unibet",
    "pinnacle": "Pinnacle",
    "betfair": "Betfair",
    "williamhill": "William Hill",
    "ladbrokes": "Ladbrokes",
    "1xbet": "1xBet",
    "bwin": "bwin",
    "betsson": "Betsson",
    "marathonbet": "Marathonbet",
    "cloudbet": "Cloudbet",
    "10bet": "10Bet",
    "188bet": "188Bet",
    "betvictor": "BetVictor",
    "matchbook": "Matchbook",
}

MARKET_CODE_MAP: dict[str, tuple[str, str]] = {
    "1x2": ("h2h", "moneyline"),
    "h2h": ("h2h", "moneyline"),
    "moneyline": ("h2h", "moneyline"),
    "ml": ("h2h", "moneyline"),
    "12": ("h2h", "moneyline"),
    "ou": ("totals", "totals"),
    "totals": ("totals", "totals"),
    "total": ("totals", "totals"),
    "handicap": ("spreads", "spreads"),
    "spread": ("spreads", "spreads"),
    "spreads": ("spreads", "spreads"),
    "ah": ("spreads", "asian_spreads"),
    "btts": ("btts", "btts"),
    "ggng": ("btts", "btts"),
    "drawnobet": ("dnb", "dnb"),
    "dnb": ("dnb", "dnb"),
    "doublechance": ("doubleChance", "double_chance"),
    "dc": ("doubleChance", "double_chance"),
    "teamtotals": ("teamTotals", "team_totals"),
    "teamtotal": ("teamTotals", "team_totals"),
    "home_total": ("teamTotals", "team_totals"),
    "away_total": ("teamTotals", "team_totals"),
}


class BookiesApiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.bookies_api_base_url or "https://bookiesapi.com/api/get.php").rstrip("/")

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_bookies_api),
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
        preview: dict[str, Any] = {"matched_examples": [], "unmatched_events": [], "response_debug": []}
        if not self.settings.enable_bookies_api or not matches:
            return {}, stats, preview
        if not self.settings.bookies_api_login or not (self.settings.bookies_api_token or self.settings.bookies_api_key):
            preview["skip_reason"] = "missing_login_or_token"
            return {}, stats, preview

        selected_matches = [m for m in matches if m.sport_key in set(self.settings.bookies_api_sports)]
        if self.settings.bookies_api_use_for_backfill_only:
            selected_matches = list(matches)
        selected_matches = selected_matches[: self.settings.max_matches_for_odds_fetch]
        stats["candidate_matches"] = len(selected_matches)
        if not selected_matches:
            return {}, stats, preview

        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in selected_matches:
            grouped[match.sport_key].append(match)

        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        async with httpx.AsyncClient(timeout=self.settings.bookies_api_timeout_seconds) as client:
            for sport_key, sport_matches in grouped.items():
                sport_slug = SPORT_MAP.get(sport_key)
                if not sport_slug:
                    continue
                event_map, unmatched = await self._fetch_and_map_events(client, sport_slug, sport_matches, stats)
                preview["unmatched_events"].extend(unmatched[:10])
                stats["events_matched"] += len(event_map)
                for event_id, entry in list(event_map.items())[:10]:
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
                for event_id, entry in event_map.items():
                    rows = await self._fetch_odds_payload(client, event_id, stats)
                    if rows is None:
                        continue
                    parsed_offers, markets_parsed = self._parse_event_odds(rows, entry["match"], entry["mode"])
                    stats["markets_parsed"] += markets_parsed
                    stats["offers_parsed"] += len(parsed_offers)
                    if parsed_offers:
                        offers_by_match[entry["match"].match_key].extend(parsed_offers)
                    else:
                        stats["unmatched_offer_events"] += 1
                    preview["response_debug"].append(
                        {
                            "event_id": event_id,
                            "markets_parsed": markets_parsed,
                            "offers_parsed": len(parsed_offers),
                            "top_level_type": type(rows).__name__,
                        }
                    )
        return {key: value for key, value in offers_by_match.items()}, stats, preview

    async def _fetch_and_map_events(
        self,
        client: httpx.AsyncClient,
        sport_slug: str,
        matches: list[Match],
        stats: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        day_keys = sorted({m.commence_time.astimezone(UTC).date().isoformat() for m in matches})
        mapping: dict[str, dict[str, Any]] = {}
        unmatched: list[dict[str, Any]] = []
        best_for_match: dict[str, tuple[str, float]] = {}
        for day_key in day_keys:
            for page in range(1, max(1, self.settings.bookies_api_max_pages_per_day) + 1):
                params = self._auth_params(
                    "predatapage",
                    {
                        "sport": sport_slug,
                        "day": day_key.replace("-", ""),
                        "p": page,
                    },
                )
                stats["event_requests"] += 1
                try:
                    response = await client.get(self.base_url, params=params)
                except Exception:
                    stats["response_errors"] += 1
                    break
                stats.setdefault("event_http_statuses", []).append(response.status_code)
                stats["last_body_preview"] = response.text[:800]
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    break
                try:
                    payload = response.json()
                except Exception:
                    stats["response_errors"] += 1
                    break
                stats.setdefault("payload_shapes", []).append("events:" + self._shape(payload))
                events = self._extract_event_list(payload)
                stats["events_fetched"] += len(events)
                if not events:
                    break
                for raw_event in events:
                    event = self._parse_event(raw_event)
                    if event is None:
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
                            event_home=event["home"],
                            event_away=event["away"],
                            event_start=event["date"],
                            event_league=event["league"],
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
                                "event_id": event["id"],
                                "home": event["home"],
                                "away": event["away"],
                                "league": event["league"],
                                "reason": "no_match",
                            }
                        )
                        continue
                    previous = best_for_match.get(best_match.match_key)
                    if previous is not None and previous[1] >= best_score:
                        continue
                    if previous is not None:
                        mapping.pop(previous[0], None)
                    mapping[event["id"]] = {
                        "match": best_match,
                        "mode": best_mode,
                        "score": best_score,
                        "event": event,
                    }
                    best_for_match[best_match.match_key] = (event["id"], best_score)
                if len(events) < self.settings.bookies_api_page_limit:
                    break
        for entry in mapping.values():
            mode = entry["mode"]
            if mode == "exact":
                stats["matched_exact"] += 1
            elif mode == "loose":
                stats["matched_loose"] += 1
            else:
                stats["matched_fuzzy"] += 1
        return mapping, unmatched

    async def _fetch_odds_payload(self, client: httpx.AsyncClient, event_id: str, stats: dict[str, Any]) -> Any | None:
        task = self.settings.bookies_api_odds_task or "allodds"
        payload = await self._fetch_task(client, task, event_id, stats)
        if payload is None and task.lower() == "allodds":
            payload = await self._fetch_task(client, "odds", event_id, stats)
        return payload

    async def _fetch_task(self, client: httpx.AsyncClient, task: str, event_id: str, stats: dict[str, Any]) -> Any | None:
        params = self._auth_params(task, {"game_id": event_id})
        stats["odds_requests"] += 1
        try:
            response = await client.get(self.base_url, params=params)
        except Exception:
            stats["response_errors"] += 1
            return None
        stats.setdefault("odds_http_statuses", []).append(response.status_code)
        stats["last_body_preview"] = response.text[:1200]
        if response.status_code != 200:
            stats["response_errors"] += 1
            return None
        try:
            payload = response.json()
        except Exception:
            stats["response_errors"] += 1
            return None
        stats.setdefault("payload_shapes", []).append(task + ":" + self._shape(payload))
        return payload

    def _parse_event_odds(self, payload: Any, match: Match, match_mode: str) -> tuple[list[Offer], int]:
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, str, str, str]] = set()
        markets_parsed = 0

        def emit(
            *,
            bookmaker: str,
            family: str,
            selection: str,
            price: float | None,
            point: float | None = None,
            team_side: str | None = None,
            market_name: str = "",
            market_key: str = "",
            market_subtype: str = "",
        ) -> None:
            if price is None or price <= 1.0 or not bookmaker:
                return
            key = (
                normalize_bookmaker_name(bookmaker),
                family,
                selection.lower(),
                "" if point is None else f"{point:.2f}",
                team_side or "",
                market_subtype or "",
            )
            if key in seen:
                return
            seen.add(key)
            offers.append(
                Offer(
                    source="bookies_api",
                    bookmaker=bookmaker,
                    family=family,  # type: ignore[arg-type]
                    selection=selection,
                    price=float(price),
                    point=point,
                    team_side=team_side,
                    market_name=market_name,
                    market_key=market_key,
                    market_subtype=market_subtype,
                    metadata={"match_mode": match_mode},
                )
            )

        def parse_outcomes(
            outcome_rows: list[Any],
            bookmaker: str,
            family: str,
            subtype: str,
            market_name: str,
            market_key: str,
            point: float | None,
        ) -> bool:
            local_emitted = False
            for item in outcome_rows:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("label") or item.get("title") or item.get("outcome") or item.get("selection") or item.get("value") or "")
                price = self._to_float(item.get("price") or item.get("odd") or item.get("odds") or item.get("value") or item.get("koef"))
                row_point = self._first_float(item, ["point", "line", "total", "handicap", "hdp"]) or point
                if family == "h2h":
                    selection = get_outcome_key(name, match.home_team, match.away_team)
                    if selection == "home":
                        emit(bookmaker=bookmaker, family=family, selection=match.home_team, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                    elif selection == "away":
                        emit(bookmaker=bookmaker, family=family, selection=match.away_team, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                    elif selection == "draw":
                        emit(bookmaker=bookmaker, family=family, selection="Draw", price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "doubleChance":
                    raw = name.strip().upper().replace(" ", "")
                    if raw in {"1X", "X2", "12"}:
                        emit(bookmaker=bookmaker, family=family, selection=raw, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "dnb":
                    selection = get_outcome_key(name, match.home_team, match.away_team)
                    if selection == "home":
                        emit(bookmaker=bookmaker, family=family, selection=match.home_team, price=price, point=0.0, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                    elif selection == "away":
                        emit(bookmaker=bookmaker, family=family, selection=match.away_team, price=price, point=0.0, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "btts":
                    raw = name.strip().lower()
                    if raw in {"yes", "y", "gg", "both teams to score - yes"}:
                        emit(bookmaker=bookmaker, family=family, selection="Yes", price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                    elif raw in {"no", "n", "ng", "both teams to score - no"}:
                        emit(bookmaker=bookmaker, family=family, selection="No", price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "totals":
                    total_sel = get_total_selection_key(name)
                    if total_sel and row_point is not None:
                        emit(bookmaker=bookmaker, family=family, selection=total_sel.title(), price=price, point=row_point, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "teamTotals":
                    total_sel = get_total_selection_key(name)
                    team_side = infer_team_total_side(market_name, market_key, name, match.home_team, match.away_team)
                    if total_sel and row_point is not None and team_side:
                        emit(bookmaker=bookmaker, family=family, selection=total_sel.title(), price=price, point=row_point, team_side=team_side, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                elif family == "spreads":
                    side = get_spread_selection_key(name, match.home_team, match.away_team)
                    if side == "home" and row_point is not None:
                        emit(bookmaker=bookmaker, family=family, selection=match.home_team, price=price, point=row_point, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
                    elif side == "away" and row_point is not None:
                        emit(bookmaker=bookmaker, family=family, selection=match.away_team, price=price, point=row_point, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        local_emitted = True
            return local_emitted

        def infer_market(market_key: str, market_name: str, path_text: str) -> tuple[str, str] | None:
            for candidate in [market_key, market_name, path_text]:
                direct = MARKET_CODE_MAP.get(self._slug(candidate))
                if direct:
                    return direct
                detected = detect_market_family(market_key or candidate, market_name or candidate, match.sport_key)
                if detected:
                    return detected
            return None

        def walk(node: Any, ctx: dict[str, Any], depth: int) -> None:
            nonlocal markets_parsed
            if node is None or depth > 8:
                return
            if isinstance(node, list):
                if ctx.get("family") and parse_outcomes(node, ctx.get("bookmaker", ""), ctx["family"], ctx.get("subtype", ""), ctx.get("market_name", ""), ctx.get("market_key", ""), ctx.get("point")):
                    markets_parsed += 1
                    return
                for item in node:
                    walk(item, dict(ctx), depth + 1)
                return
            if not isinstance(node, dict):
                return

            next_ctx = dict(ctx)
            next_ctx["bookmaker"] = self._pick_bookmaker(
                next_ctx.get("bookmaker"),
                node.get("bookmaker"),
                node.get("bookie"),
                node.get("company"),
                node.get("source"),
                node.get("site"),
                node.get("title"),
                node.get("name"),
            )
            next_ctx["market_key"] = str(node.get("market_key") or next_ctx.get("market_key") or "")
            next_ctx["market_name"] = str(node.get("market_name") or node.get("market") or node.get("type") or node.get("bet") or node.get("wager") or next_ctx.get("market_name") or "")
            next_ctx["point"] = self._first_float(node, ["point", "line", "total", "handicap", "hdp"]) or next_ctx.get("point")
            info = infer_market(next_ctx.get("market_key", ""), next_ctx.get("market_name", ""), " > ".join(next_ctx.get("path", [])))
            if info:
                next_ctx["family"], next_ctx["subtype"] = info
            for outcomes_key in ["outcomes", "selections", "odds", "prices", "results"]:
                value = node.get(outcomes_key)
                if isinstance(value, list) and next_ctx.get("family"):
                    if parse_outcomes(value, next_ctx.get("bookmaker", ""), next_ctx["family"], next_ctx.get("subtype", ""), next_ctx.get("market_name", ""), next_ctx.get("market_key", ""), next_ctx.get("point")):
                        markets_parsed += 1
            # direct dict rows like {home: 2.1, draw: 3.2, away: 3.5}
            if next_ctx.get("family") and next_ctx.get("bookmaker"):
                direct_done = self._parse_direct_prices(node, next_ctx, match, emit)
                if direct_done:
                    markets_parsed += 1
            for key, value in node.items():
                if not isinstance(value, (dict, list)):
                    continue
                child = dict(next_ctx)
                child_path = list(child.get("path", []))
                child_path.append(str(key))
                child["path"] = child_path
                key_slug = self._slug(key)
                if not child.get("bookmaker") and key_slug in KNOWN_BOOKMAKERS:
                    child["bookmaker"] = KNOWN_BOOKMAKERS[key_slug]
                if key_slug in MARKET_CODE_MAP and not child.get("market_key"):
                    child["market_key"] = str(key)
                    child["market_name"] = str(key)
                    child["family"], child["subtype"] = MARKET_CODE_MAP[key_slug]
                walk(value, child, depth + 1)

        walk(payload, {"path": []}, 0)
        return offers, markets_parsed

    def _parse_direct_prices(self, node: dict[str, Any], ctx: dict[str, Any], match: Match, emit) -> bool:
        bookmaker = str(ctx.get("bookmaker") or "")
        family = str(ctx.get("family") or "")
        subtype = str(ctx.get("subtype") or "")
        market_name = str(ctx.get("market_name") or "")
        market_key = str(ctx.get("market_key") or "")
        point = ctx.get("point")
        emitted = False
        if family == "h2h":
            for key, selection in [("home", match.home_team), ("draw", "Draw"), ("away", match.away_team), ("1", match.home_team), ("x", "Draw"), ("2", match.away_team)]:
                price = self._to_float(node.get(key))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=selection, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "doubleChance":
            for key, selection in [("1x", "1X"), ("x2", "X2"), ("12", "12")]:
                price = self._to_float(node.get(key))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=selection, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "dnb":
            for key, selection in [("home", match.home_team), ("away", match.away_team), ("1", match.home_team), ("2", match.away_team)]:
                price = self._to_float(node.get(key))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=selection, price=price, point=0.0, market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "btts":
            for key, selection in [("yes", "Yes"), ("no", "No"), ("1", "Yes"), ("2", "No")]:
                price = self._to_float(node.get(key))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=selection, price=price, market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "totals" and point is not None:
            for key, selection in [("over", "Over"), ("under", "Under"), ("1", "Under"), ("2", "Over")]:
                price = self._to_float(node.get(key))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=selection, price=price, point=float(point), market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "spreads":
            home_line = self._first_float(node, ["home_line", "homeHandicap", "home_spread", "homeSpread"])
            away_line = self._first_float(node, ["away_line", "awayHandicap", "away_spread", "awaySpread"])
            raw_line = self._first_float(node, ["line", "point", "handicap", "hdp"])
            if home_line is None and away_line is None and raw_line is not None:
                home_line = raw_line
                away_line = -raw_line
            elif home_line is None and away_line is not None:
                home_line = -away_line
            elif away_line is None and home_line is not None:
                away_line = -home_line
            if home_line is not None:
                price = self._to_float(node.get("home") or node.get("1"))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=match.home_team, price=price, point=float(home_line), market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
            if away_line is not None:
                price = self._to_float(node.get("away") or node.get("2"))
                if price and price > 1.0:
                    emit(bookmaker=bookmaker, family=family, selection=match.away_team, price=price, point=float(away_line), market_name=market_name, market_key=market_key, market_subtype=subtype)
                    emitted = True
        elif family == "teamTotals" and point is not None:
            team_side = infer_team_total_side(market_name, market_key, "", match.home_team, match.away_team)
            if team_side:
                for key, selection in [("over", "Over"), ("under", "Under")]:
                    price = self._to_float(node.get(key))
                    if price and price > 1.0:
                        emit(bookmaker=bookmaker, family=family, selection=selection, price=price, point=float(point), team_side=team_side, market_name=market_name, market_key=market_key, market_subtype=subtype)
                        emitted = True
        return emitted

    def _extract_event_list(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ["data", "results", "response", "games", "matches"]:
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        def walk(node: Any, depth: int) -> None:
            if node is None or depth > 6:
                return
            if isinstance(node, list):
                for item in node:
                    walk(item, depth + 1)
                return
            if not isinstance(node, dict):
                return
            parsed = self._parse_event(node)
            if parsed is not None and parsed["id"] not in seen:
                seen.add(parsed["id"])
                found.append(node)
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value, depth + 1)
        walk(payload, 0)
        return found

    def _parse_event(self, item: dict[str, Any]) -> dict[str, Any] | None:
        home = self._team_name(
            item.get("home_team") or item.get("homeTeam") or item.get("home") or item.get("team_home") or item.get("team1") or item.get("team1_name") or item.get("home_name") or item.get("localteam") or item.get("local") or (item.get("teams") or {}).get("home")
        )
        away = self._team_name(
            item.get("away_team") or item.get("awayTeam") or item.get("away") or item.get("team_away") or item.get("team2") or item.get("team2_name") or item.get("away_name") or item.get("visitorteam") or item.get("visitor") or (item.get("teams") or {}).get("away")
        )
        if (not home or not away) and isinstance(item.get("teams"), list) and len(item["teams"]) >= 2:
            home = home or self._team_name(item["teams"][0])
            away = away or self._team_name(item["teams"][1])
        event_id = str(item.get("id") or item.get("game_id") or item.get("gameid") or item.get("event_id") or item.get("match_id") or "")
        date_value = item.get("event_date") or item.get("start_time") or item.get("commence_time") or item.get("kickoff") or item.get("date") or item.get("match_time") or item.get("time") or item.get("datetime") or item.get("start") or item.get("starts_at") or item.get("startAt") or item.get("match_date") or item.get("event_time") or item.get("ts") or item.get("timestamp")
        if not (home and away and event_id and date_value):
            return None
        try:
            event_dt = self._parse_date_value(date_value)
        except Exception:
            return None
        league = str(
            ((item.get("league") or {}).get("name") if isinstance(item.get("league"), dict) else "")
            or ((item.get("competition") or {}).get("name") if isinstance(item.get("competition"), dict) else "")
            or ((item.get("tournament") or {}).get("name") if isinstance(item.get("tournament"), dict) else "")
            or item.get("league_name")
            or item.get("competition_name")
            or item.get("tournament_name")
            or item.get("championship")
            or item.get("league")
            or item.get("competition")
            or ""
        )
        return {"id": event_id, "home": home, "away": away, "date": event_dt, "league": league}

    def _auth_params(self, task: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {
            "login": self.settings.bookies_api_login or "",
            "token": self.settings.bookies_api_token or self.settings.bookies_api_key or "",
            "task": task,
        }
        if extra:
            params.update(extra)
        return params

    @staticmethod
    def _slug(value: Any) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum() or ch in {"_"})

    @staticmethod
    def _shape(value: Any) -> str:
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return ",".join(sorted(value.keys())[:12])
        return type(value).__name__

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _first_float(mapping: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            try:
                value = mapping.get(key)
            except Exception:
                value = None
            try:
                if value is None or value == "":
                    continue
                return float(value)
            except Exception:
                continue
        return None

    @staticmethod
    def _pick_bookmaker(*values: Any) -> str:
        for value in values:
            raw = str(value or "").strip()
            if not raw:
                continue
            normalized = normalize_bookmaker_name(raw)
            if normalized in KNOWN_BOOKMAKERS:
                return KNOWN_BOOKMAKERS[normalized]
            return raw
        return ""

    @staticmethod
    def _team_name(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            return str(
                value.get("name") or value.get("team_name") or value.get("teamName") or value.get("title") or value.get("short_name") or value.get("shortName") or value.get("common_name") or value.get("commonName") or value.get("full_name") or value.get("fullName") or value.get("abbr") or value.get("code") or ""
            )
        return ""

    @staticmethod
    def _parse_date_value(value: Any) -> datetime:
        if isinstance(value, (int, float)):
            number = int(value)
            if number > 1_000_000_000_000:
                return datetime.fromtimestamp(number / 1000, tz=UTC)
            if number > 1_000_000_000:
                return datetime.fromtimestamp(number, tz=UTC)
        raw = str(value or "").strip()
        if raw.isdigit() and len(raw) in {10, 13}:
            number = int(raw)
            if len(raw) == 13:
                return datetime.fromtimestamp(number / 1000, tz=UTC)
            return datetime.fromtimestamp(number, tz=UTC)
        raw = raw.replace("/", "-")
        if " " in raw and "T" not in raw:
            raw = raw.replace(" ", "T", 1)
        return parse_datetime(raw)
