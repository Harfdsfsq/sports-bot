from __future__ import annotations

from collections import defaultdict
import os
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
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
    is_simulated_or_esports_event,
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
        credentials_present = bool(
            getattr(self.settings, "bookies_api_login", None)
            and (
                getattr(self.settings, "bookies_api_token", None)
                or getattr(self.settings, "bookies_api_key", None)
            )
        )
        enabled = bool(getattr(self.settings, "bookies_api_enabled", False)) or credentials_present
        stats: dict[str, Any] = {
            "enabled": enabled,
            "credentials_present": credentials_present,
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
            "empty_offer_payloads": 0,
            "event_http_statuses": [],
            "odds_http_statuses": [],
            "payload_shapes": [],
            "bookmakers_seen": 0,
            "last_body_preview": None,
            "simulated_skipped": 0,
            "task_used": self.settings.bookies_api_odds_task,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_odds": []}

        if not enabled:
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
                            if isinstance(raw, dict):
                                league_name = self._extract_league(raw)
                                try:
                                    home_name = str(raw.get("home") or raw.get("home_team") or raw.get("team1") or "")
                                    away_name = str(raw.get("away") or raw.get("away_team") or raw.get("team2") or "")
                                except Exception:
                                    home_name = ""
                                    away_name = ""
                                if is_simulated_or_esports_event(home_name, away_name, league_name):
                                    stats["simulated_skipped"] += 1
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

            matched_items = list(matched_events.items())
            matched_items.sort(key=lambda row: self._matched_event_priority(row[1]))
            odds_fetch_limit = int(os.getenv("BOOKIES_API_ODDS_FETCH_LIMIT", str(getattr(self.settings, "bookies_api_odds_fetch_limit", 0) or 0)) or 0)
            if odds_fetch_limit > 0 and len(matched_items) > odds_fetch_limit:
                matched_items = matched_items[:odds_fetch_limit]
                stats["odds_fetch_limited_to"] = odds_fetch_limit

            for match_key, item in matched_items:
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
            if is_simulated_or_esports_event(match.home_team, match.away_team, match.league_name):
                continue
            if not self.settings.allow_low_tier and match.tier == "low":
                continue
            merged_existing: list[Offer] = []
            for source_map in existing_offer_maps.values():
                merged_existing.extend(source_map.get(match.match_key) or [])
            has_h2h = any(offer.family == "h2h" for offer in merged_existing)
            if self.settings.bookies_api_use_for_backfill_only and has_h2h:
                continue
            out.append(match)
        now = datetime.now(UTC)
        publish_cutoff = now + timedelta(hours=max(1, int(self.settings.publish_window_hours or 48)))
        out.sort(key=lambda match: self._candidate_priority(match, now, publish_cutoff))
        return out

    @staticmethod
    def _candidate_priority(match: Match, now: datetime, publish_cutoff: datetime) -> tuple[int, int, int, float, str, str, str]:
        in_window = 0 if now <= match.commence_time <= publish_cutoff else 1
        tier_rank = 0 if match.tier == "top" else 1 if match.tier == "mid" else 2
        has_bet365 = 0 if match.metadata.get("bet365_id") else 1
        kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
        return (
            in_window,
            tier_rank,
            has_bet365,
            kickoff_distance,
            match.league_name.lower(),
            match.home_team.lower(),
            match.away_team.lower(),
        )

    @staticmethod
    def _candidate_lookup_by_date(matches: list[Match]) -> dict[str, list[Match]]:
        out: dict[str, list[Match]] = defaultdict(list)
        for match in matches:
            base = match.commence_time.astimezone(UTC).date()
            for offset in (-1, 0, 1):
                out[(base + timedelta(days=offset)).isoformat()].append(match)
        return out

    @staticmethod
    def _matched_event_priority(item: dict[str, Any]) -> tuple[int, int, int, float, str, str, str]:
        match = item.get('match')
        event = item.get('event') or {}
        if match is None:
            return (3, 3, 1, 9999.0, '', '', '')

        quality_rank_map = {'exact': 0, 'loose': 1, 'fuzzy': 2}
        tier_rank_map = {'top': 0, 'mid': 1, 'low': 2}

        quality_rank = quality_rank_map.get(str(event.get('match_quality') or '').lower(), 3)
        tier_rank = tier_rank_map.get(str(getattr(match, 'tier', '') or '').lower(), 3)
        has_bet365 = 0 if getattr(match, 'metadata', {}).get('bet365_id') else 1
        now = datetime.now(UTC)
        kickoff_distance = abs((getattr(match, 'commence_time', now) - now).total_seconds()) / 3600.0
        return (
            quality_rank,
            tier_rank,
            has_bet365,
            kickoff_distance,
            str(getattr(match, 'league_name', '')).lower(),
            str(getattr(match, 'home_team', '')).lower(),
            str(getattr(match, 'away_team', '')).lower(),
        )

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
        if is_simulated_or_esports_event(home, away, league):
            return None

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
            stats["empty_offer_payloads"] = int(stats.get("empty_offer_payloads", 0)) + 1
        return parsed, payload

    def _parse_odds_payload(self, payload: Any, match: Match) -> list[Offer]:
        if not isinstance(payload, dict):
            return []

        if "games_pre" in payload:
            return []

        odds = payload.get("odds")
        if not isinstance(odds, dict):
            return []

        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None, str | None]] = set()

        def add_offer(
            bookmaker: str,
            family: str,
            selection: str,
            price_value: Any,
            *,
            point: Any = None,
            market_name: str = "",
            market_key: str = "",
            team_side: str | None = None,
        ) -> None:
            book = self._canonical_bookmaker_name(bookmaker)
            price = self._to_float(price_value)
            point_value = self._to_float(point)

            if not book or price is None or price <= 1.0:
                return

            key = (book, family, selection, point_value, team_side)
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
                    point=point_value,
                    team_side=team_side,
                    market_name=market_name,
                    market_key=market_key,
                    market_subtype="bookies_api",
                    metadata={"bookies_api": True},
                )
            )

        def parse_three_way(bookmaker: str, row: dict[str, Any], stage: str) -> None:
            add_offer(
                bookmaker,
                "h2h",
                match.home_team,
                row.get("home_od"),
                market_name=f"1X2 {stage}",
                market_key="h2h",
            )
            add_offer(
                bookmaker,
                "h2h",
                "Draw",
                row.get("draw_od"),
                market_name=f"1X2 {stage}",
                market_key="h2h",
            )
            add_offer(
                bookmaker,
                "h2h",
                match.away_team,
                row.get("away_od"),
                market_name=f"1X2 {stage}",
                market_key="h2h",
            )

        def parse_spread(bookmaker: str, row: dict[str, Any], stage: str) -> None:
            handicap = self._to_float(row.get("handicap"))
            if handicap is None:
                return

            home_price = self._to_float(row.get("home_od"))
            away_price = self._to_float(row.get("away_od"))

            if home_price is not None and home_price > 1.0:
                add_offer(
                    bookmaker,
                    "spreads",
                    match.home_team,
                    home_price,
                    point=handicap,
                    market_name=f"Handicap {stage}",
                    market_key="spreads",
                    team_side="home",
                )

            if away_price is not None and away_price > 1.0:
                add_offer(
                    bookmaker,
                    "spreads",
                    match.away_team,
                    away_price,
                    point=-handicap,
                    market_name=f"Handicap {stage}",
                    market_key="spreads",
                    team_side="away",
                )

        def parse_totals(bookmaker: str, row: dict[str, Any], stage: str) -> None:
            total = self._to_float(row.get("handicap"))
            if total is None:
                return

            add_offer(
                bookmaker,
                "totals",
                "Over",
                row.get("over_od"),
                point=total,
                market_name=f"Totals {stage}",
                market_key="totals",
            )
            add_offer(
                bookmaker,
                "totals",
                "Under",
                row.get("under_od"),
                point=total,
                market_name=f"Totals {stage}",
                market_key="totals",
            )

        for bookmaker, bookmaker_payload in odds.items():
            if not isinstance(bookmaker_payload, dict):
                continue

            for stage in ("prematch", "kickoff", "live"):
                rows = bookmaker_payload.get(stage)
                if not isinstance(rows, list):
                    continue

                if len(rows) >= 1 and isinstance(rows[0], dict):
                    parse_three_way(bookmaker, rows[0], stage)

                if len(rows) >= 2 and isinstance(rows[1], dict):
                    parse_spread(bookmaker, rows[1], stage)

                if len(rows) >= 3 and isinstance(rows[2], dict):
                    parse_totals(bookmaker, rows[2], stage)

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
            return float(str(value).strip().replace("%", "").replace(",", "."))
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
