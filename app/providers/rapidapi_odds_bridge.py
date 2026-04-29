from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import normalize_bookmaker_name, parse_datetime, score_event_match

UTC = timezone.utc


class RapidApiOddsBridgeProvider:
    """Best-effort odds bridge for RapidAPI football odds providers.

    Hosts supported from user-provided RapidAPI docs/hosts:
      - sportsbook-api2.p.rapidapi.com
      - odds-feed.p.rapidapi.com
      - odds-api1.p.rapidapi.com
      - sportapi7.p.rapidapi.com
      - free-api-live-football-data.p.rapidapi.com

    The provider is intentionally conservative:
      - small per-run request budgets;
      - cache-first behaviour;
      - generic parsing that only emits offers when event and market shapes are clear;
      - detailed diagnostics for endpoint/schema tuning.
    """

    VERSION = "rapidapi-odds-bridge-v1-2026-04-29"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = float(os.getenv("RAPIDAPI_ODDS_TIMEOUT_SECONDS", "14") or 14)
        self.cache_ttl_minutes = max(5, int(float(os.getenv("RAPIDAPI_ODDS_CACHE_TTL_MINUTES", "45") or 45)))
        self.match_limit = max(1, int(float(os.getenv("RAPIDAPI_ODDS_MATCH_LIMIT", "36") or 36)))
        self.max_requests = max(0, int(float(os.getenv("RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN", "24") or 24)))
        self.enabled = self._env_bool("ENABLE_RAPIDAPI_ODDS_BRIDGE", True)
        self.providers = self._build_providers()

    def _build_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "sportsbook_api",
                "source": "sportsbook_api",
                "host": os.getenv("SPORTSBOOK_RAPIDAPI_HOST", "sportsbook-api2.p.rapidapi.com"),
                "key_envs": ["SPORTSBOOK_RAPIDAPI_KEY", "RAPIDAPI_KEY"],
                "max_requests": int(float(os.getenv("SPORTSBOOK_API_RAPIDAPI_PER_RUN_MAX", "8") or 8)),
                "event_paths": [
                    "/v0/events/",
                    "/v0/events/?sport=SOCCER",
                    "/v0/events/?sport=soccer",
                ],
                "market_paths": [
                    "/v0/events/{event_key}/markets",
                    "/v0/events/{event_key}/markets/",
                ],
                "outcome_paths": [
                    "/v1/markets/{market_key}/outcomes/latest",
                    "/v1/markets/{market_key}/outcomes/last",
                    "/v0/markets/{market_key}/outcomes/latest",
                    "/v0/markets/{market_key}/outcomes/last",
                    "/v0/markets/{market_key}/outcomes",
                ],
            },
            {
                "key": "odds_feed",
                "source": "odds_feed",
                "host": os.getenv("ODDS_FEED_RAPIDAPI_HOST", "odds-feed.p.rapidapi.com"),
                "key_envs": ["ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY"],
                "max_requests": int(float(os.getenv("ODDS_FEED_RAPIDAPI_PER_RUN_MAX", "6") or 6)),
                "event_paths": [
                    "/api/v1/events",
                    "/api/v1/events?sport=football",
                    "/api/v1/events?sport=soccer",
                    "/api/events",
                    "/events",
                ],
                "market_paths": [
                    "/api/v1/events/{event_key}/markets",
                    "/api/v1/event/{event_key}/markets",
                    "/api/v1/event-markets?event_id={event_key}",
                    "/api/event-markets?event_id={event_key}",
                ],
                "outcome_paths": [],
            },
            {
                "key": "odds_api1",
                "source": "odds_api1",
                "host": os.getenv("ODDS_API1_RAPIDAPI_HOST", "odds-api1.p.rapidapi.com"),
                "key_envs": ["ODDS_API1_RAPIDAPI_KEY", "ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY"],
                "max_requests": int(float(os.getenv("ODDS_API1_RAPIDAPI_PER_RUN_MAX", "4") or 4)),
                "event_paths": [
                    "/api/v1/events",
                    "/events",
                    "/sports/soccer/events",
                    "/odds?sport=soccer",
                ],
                "market_paths": [
                    "/api/v1/events/{event_key}/markets",
                    "/events/{event_key}/markets",
                    "/odds?eventId={event_key}",
                ],
                "outcome_paths": [],
            },
            {
                "key": "sportapi7",
                "source": "sportapi7",
                "host": os.getenv("SPORTAPI7_RAPIDAPI_HOST", "sportapi7.p.rapidapi.com"),
                "key_envs": ["SPORTAPI7_RAPIDAPI_KEY", "RAPIDAPI_KEY"],
                "max_requests": int(float(os.getenv("SPORTAPI7_RAPIDAPI_ODDS_PER_RUN_MAX", "4") or 4)),
                "event_paths": [
                    f"/api/v1/sport/football/scheduled-events/{datetime.now(UTC).date().isoformat()}",
                    "/api/v1/sport/football/events/live",
                ],
                "market_paths": [
                    "/api/v1/event/{event_key}/odds",
                    "/api/v1/event/{event_key}/featured-odds",
                    "/api/v1/event/{event_key}/all-odds",
                    "/api/v1/event/{event_key}/allodds",
                ],
                "outcome_paths": [],
            },
            {
                "key": "free_live_football_data",
                "source": "free_live_football_data",
                "host": os.getenv("FREE_FOOTBALL_RAPIDAPI_HOST", "free-api-live-football-data.p.rapidapi.com"),
                "key_envs": ["FREE_FOOTBALL_RAPIDAPI_KEY", "RAPIDAPI_KEY"],
                "max_requests": int(float(os.getenv("FREE_FOOTBALL_RAPIDAPI_ODDS_PER_RUN_MAX", "4") or 4)),
                "event_paths": [
                    f"/football-matches?date={datetime.now(UTC).date().isoformat()}",
                    f"/football-fixtures?date={datetime.now(UTC).date().isoformat()}",
                    "/football-matches-today",
                ],
                "market_paths": [
                    "/football-odds?event_id={event_key}",
                    "/football-match-odds?event_id={event_key}",
                    "/football-odds?match_id={event_key}",
                ],
                "outcome_paths": [],
            },
        ]

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "provider_version": self.VERSION,
            "enabled": self.enabled,
            "requests": 0,
            "response_errors": 0,
            "cache_hit": False,
            "max_http_requests_per_run": self.max_requests,
            "budget_exhausted": False,
            "events_fetched": 0,
            "events_matched": 0,
            "market_requests": 0,
            "outcome_requests": 0,
            "offers_parsed": 0,
            "providers": {},
            "http_statuses": [],
            "last_url": None,
            "last_error": None,
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_markets": [], "sample_offers": [], "matched_examples": []}
        if not self.enabled:
            return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        cached = self._load_cache(soccer_matches)
        if cached is not None:
            stats["cache_hit"] = True
            return cached, stats, preview

        target_matches = self._prioritize_matches(soccer_matches)[: self.match_limit]
        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for provider in self.providers:
                if self._budget_exhausted(stats):
                    break
                key = self._api_key(provider)
                provider_stats = {
                    "host": provider["host"],
                    "api_key_present": bool(key),
                    "requests": 0,
                    "events_fetched": 0,
                    "events_matched": 0,
                    "offers_parsed": 0,
                    "errors": 0,
                }
                stats["providers"][provider["key"]] = provider_stats
                if not key:
                    continue
                await self._fetch_provider_offers(client, provider, key, target_matches, offers_by_match, stats, provider_stats, preview)

        output = {k: v for k, v in offers_by_match.items() if v}
        self._write_cache(output)
        return output, stats, preview

    async def _fetch_provider_offers(
        self,
        client: httpx.AsyncClient,
        provider: dict[str, Any],
        key: str,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        stats: dict[str, Any],
        provider_stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> None:
        headers = {
            "x-rapidapi-host": provider["host"],
            "x-rapidapi-key": key,
            "accept": "application/json",
        }
        provider_request_start = int(provider_stats.get("requests", 0) or 0)
        event_rows: list[dict[str, Any]] = []
        for path in provider.get("event_paths") or []:
            if self._provider_budget_exhausted(provider, provider_stats) or self._budget_exhausted(stats):
                break
            payload = await self._get_json(client, provider, path, headers, stats, provider_stats)
            rows = self._event_rows(payload)
            if rows:
                event_rows = rows
                break
        stats["events_fetched"] += len(event_rows)
        provider_stats["events_fetched"] = len(event_rows)
        if event_rows and len(preview["sample_events"]) < 4:
            preview["sample_events"].append({"provider": provider["key"], "row": event_rows[0]})

        matched_events: list[tuple[Match, dict[str, Any], str]] = []
        for row in event_rows:
            matched = self._match_event(row, matches)
            if matched is None:
                continue
            matched_events.append(matched)
            if len(preview["matched_examples"]) < 8:
                match, _, quality = matched
                preview["matched_examples"].append({
                    "provider": provider["key"],
                    "match_key": match.match_key,
                    "home": match.home_team,
                    "away": match.away_team,
                    "quality": quality,
                    "event_key": self._event_key(row),
                })
        stats["events_matched"] += len(matched_events)
        provider_stats["events_matched"] = len(matched_events)

        for match, event, _quality in matched_events:
            if self._provider_budget_exhausted(provider, provider_stats) or self._budget_exhausted(stats):
                break
            event_key = self._event_key(event)
            inline_offers = self._parse_offers_from_payload(event, match, provider["source"], event_key)
            if inline_offers:
                offers_by_match[match.match_key].extend(inline_offers)
                stats["offers_parsed"] += len(inline_offers)
                provider_stats["offers_parsed"] += len(inline_offers)
                if len(preview["sample_offers"]) < 6:
                    preview["sample_offers"].extend([asdict(item) for item in inline_offers[:2]])
            if not event_key:
                continue
            market_rows: list[dict[str, Any]] = []
            for path_template in provider.get("market_paths") or []:
                if self._provider_budget_exhausted(provider, provider_stats) or self._budget_exhausted(stats):
                    break
                path = str(path_template).replace("{event_key}", str(event_key))
                payload = await self._get_json(client, provider, path, headers, stats, provider_stats)
                stats["market_requests"] += 1
                rows = self._market_rows(payload)
                if rows:
                    market_rows.extend(rows)
                    if len(preview["sample_markets"]) < 4:
                        preview["sample_markets"].append({"provider": provider["key"], "row": rows[0]})
                    break
            for market in market_rows[:10]:
                parsed = self._parse_offers_from_payload(market, match, provider["source"], event_key)
                if parsed:
                    offers_by_match[match.match_key].extend(parsed)
                    stats["offers_parsed"] += len(parsed)
                    provider_stats["offers_parsed"] += len(parsed)
                    if len(preview["sample_offers"]) < 6:
                        preview["sample_offers"].extend([asdict(item) for item in parsed[:2]])
                market_key = self._market_key(market)
                if not market_key:
                    continue
                for path_template in provider.get("outcome_paths") or []:
                    if self._provider_budget_exhausted(provider, provider_stats) or self._budget_exhausted(stats):
                        break
                    path = str(path_template).replace("{market_key}", str(market_key))
                    payload = await self._get_json(client, provider, path, headers, stats, provider_stats)
                    stats["outcome_requests"] += 1
                    parsed = self._parse_offers_from_payload(payload, match, provider["source"], event_key)
                    if parsed:
                        offers_by_match[match.match_key].extend(parsed)
                        stats["offers_parsed"] += len(parsed)
                        provider_stats["offers_parsed"] += len(parsed)
                        if len(preview["sample_offers"]) < 6:
                            preview["sample_offers"].extend([asdict(item) for item in parsed[:2]])
                        break
        provider_stats["requests_delta"] = int(provider_stats.get("requests", 0) or 0) - provider_request_start

    async def _get_json(self, client: httpx.AsyncClient, provider: dict[str, Any], path: str, headers: dict[str, str], stats: dict[str, Any], provider_stats: dict[str, Any]) -> Any | None:
        url = f"https://{provider['host']}{path}"
        if self._budget_exhausted(stats) or self._provider_budget_exhausted(provider, provider_stats):
            return None
        stats["requests"] += 1
        provider_stats["requests"] += 1
        stats["last_url"] = url
        try:
            response = await client.get(url, headers=headers)
        except Exception as exc:
            stats["response_errors"] += 1
            provider_stats["errors"] += 1
            stats["last_error"] = f"{exc.__class__.__name__}: {exc}"
            return None
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1600]
        if response.status_code == 429:
            provider_stats["rate_limited"] = True
            stats["last_error"] = "http_status=429"
            stats["response_errors"] += 1
            provider_stats["errors"] += 1
            return None
        if response.status_code < 200 or response.status_code >= 300:
            stats["last_error"] = f"http_status={response.status_code}"
            provider_stats["last_status"] = response.status_code
            provider_stats["errors"] += 1
            return None
        try:
            return response.json()
        except Exception:
            stats["response_errors"] += 1
            provider_stats["errors"] += 1
            stats["last_error"] = "json_parse_failed"
            return None

    def _match_event(self, row: dict[str, Any], matches: list[Match]) -> tuple[Match, dict[str, Any], str] | None:
        home = self._pick_text(row, ["home_team", "homeTeam", "home", "team1", "participant1Name", "home_name", "homeName"])
        away = self._pick_text(row, ["away_team", "awayTeam", "away", "team2", "participant2Name", "away_name", "awayName"])
        league = self._pick_text(row, ["league", "leagueName", "competition", "competitionName", "tournament", "tournamentName", "category"])
        start = self._event_start(row)
        if not home or not away or start is None:
            return None
        best: tuple[Match, str, float] | None = None
        for match in matches:
            score, quality = score_event_match(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=home,
                event_away=away,
                event_start=start,
                event_league=league,
                exact_tolerance_hours=float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
                fuzzy_tolerance_hours=max(float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8), 18.0),
            )
            if best is None or score > best[2]:
                best = (match, quality, score)
        if best is None or best[2] < (70.0 if best[1] == "fuzzy" else 52.0):
            return None
        return best[0], row, best[1]

    def _parse_offers_from_payload(self, payload: Any, match: Match, source: str, event_key: str | None) -> list[Offer]:
        rows = self._odds_rows(payload)
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            bookmaker = self._bookmaker(row)
            market_text = self._market_text(row)
            outcome_text = self._outcome_text(row)
            price = self._price(row)
            if price is None or price <= 1.0:
                continue
            family, selection, point, team_side = self._classify_offer(market_text, outcome_text, row, match)
            if family is None or selection is None:
                continue
            key = (bookmaker, family, selection, point)
            if key in seen:
                continue
            seen.add(key)
            offers.append(Offer(
                source=source,
                bookmaker=bookmaker,
                family=family,  # type: ignore[arg-type]
                selection=selection,
                price=float(price),
                point=point,
                team_side=team_side,
                market_name=market_text,
                market_key=self._market_key(row) or market_text,
                source_event_id=event_key,
                metadata={"rapidapi_source": source, "raw_market": market_text, "raw_outcome": outcome_text},
            ))
        return offers

    def _classify_offer(self, market: str, outcome: str, row: dict[str, Any], match: Match) -> tuple[str | None, str | None, float | None, str | None]:
        m = self._norm(market)
        o = self._norm(outcome)
        point = self._point(row, outcome + " " + market)
        if any(token in m for token in ["total", "overunder", "goals"]) or any(token in o for token in ["over", "under"]):
            if "over" in o:
                return "totals", "Over", point, None
            if "under" in o:
                return "totals", "Under", point, None
        if any(token in m for token in ["bothteamstoscore", "btts"]):
            if any(token in o for token in ["yes", "true"]):
                return "btts", "Yes", None, None
            if any(token in o for token in ["no", "false"]):
                return "btts", "No", None, None
        if any(token in m for token in ["handicap", "spread", "asian"]):
            if self._team_like(outcome, match.home_team):
                return "spreads", match.home_team, point, "home"
            if self._team_like(outcome, match.away_team):
                return "spreads", match.away_team, point, "away"
        if any(token in m for token in ["winner", "moneyline", "1x2", "h2h", "matchodds", "result"]) or o in {"1", "x", "2", "home", "away", "draw"}:
            if o in {"1", "home"} or self._team_like(outcome, match.home_team):
                return "h2h", match.home_team, None, "home"
            if o in {"2", "away"} or self._team_like(outcome, match.away_team):
                return "h2h", match.away_team, None, "away"
            if o in {"x", "draw", "tie"}:
                return "h2h", "Draw", None, None
        return None, None, None, None

    @staticmethod
    def _event_rows(payload: Any) -> list[dict[str, Any]]:
        return RapidApiOddsBridgeProvider._extract_rows(payload, keys=("events", "data", "results", "response", "fixtures", "matches", "items"))

    @staticmethod
    def _market_rows(payload: Any) -> list[dict[str, Any]]:
        return RapidApiOddsBridgeProvider._extract_rows(payload, keys=("markets", "data", "results", "response", "items"))

    @staticmethod
    def _odds_rows(payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def walk(value: Any, inherited: dict[str, Any] | None = None) -> None:
            inherited = inherited or {}
            if isinstance(value, list):
                for item in value:
                    walk(item, inherited)
            elif isinstance(value, dict):
                row = dict(inherited)
                for key in (
                    "bookmaker", "bookmakerName", "book", "sportsbook", "provider",
                    "market", "marketName", "market_key", "marketKey", "name", "label",
                    "outcome", "outcomeName", "selection", "runner", "participant", "team",
                    "price", "odds", "decimal", "decimalOdds", "value", "line", "point", "handicap",
                    "id", "key", "marketId", "marketKey",
                ):
                    if key in value and not isinstance(value.get(key), (dict, list)):
                        row[key] = value.get(key)
                has_price = any(k in row for k in ("price", "odds", "decimal", "decimalOdds", "value"))
                has_outcome = any(k in row for k in ("outcome", "outcomeName", "selection", "runner", "participant", "team", "name", "label"))
                if has_price and has_outcome:
                    rows.append(row)
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        next_inherited = dict(row)
                        norm = str(key).lower()
                        if norm in {"markets", "market", "outcomes", "runners", "books", "bookmakers"}:
                            next_inherited.setdefault("container", key)
                        if norm not in {"metadata"}:
                            walk(child, next_inherited)
        walk(payload)
        return rows

    @staticmethod
    def _extract_rows(payload: Any, *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
                if isinstance(value, dict):
                    nested = RapidApiOddsBridgeProvider._extract_rows(value, keys=keys)
                    if nested:
                        return nested
        return []

    @staticmethod
    def _pick_text(row: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("Name") or value.get("title")
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @staticmethod
    def _event_start(row: dict[str, Any]) -> datetime | None:
        for key in ("startTime", "startsAt", "commence_time", "commenceTime", "date", "eventDate", "event_date", "kickoff", "time", "startTimestamp"):
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                if key == "startTimestamp" and str(value).isdigit():
                    return datetime.fromtimestamp(float(value), tz=UTC)
                dt = parse_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except Exception:
                continue
        return None

    @staticmethod
    def _event_key(row: dict[str, Any]) -> str:
        return str(row.get("eventKey") or row.get("event_key") or row.get("eventId") or row.get("event_id") or row.get("id") or row.get("key") or "").strip()

    @staticmethod
    def _market_key(row: dict[str, Any]) -> str:
        return str(row.get("marketKey") or row.get("market_key") or row.get("marketId") or row.get("market_id") or row.get("id") or row.get("key") or "").strip()

    @staticmethod
    def _market_text(row: dict[str, Any]) -> str:
        return str(row.get("market") or row.get("marketName") or row.get("market_key") or row.get("marketKey") or row.get("container") or "").strip()

    @staticmethod
    def _outcome_text(row: dict[str, Any]) -> str:
        return str(row.get("outcome") or row.get("outcomeName") or row.get("selection") or row.get("runner") or row.get("participant") or row.get("team") or row.get("name") or row.get("label") or "").strip()

    @staticmethod
    def _bookmaker(row: dict[str, Any]) -> str:
        raw = str(row.get("bookmaker") or row.get("bookmakerName") or row.get("book") or row.get("sportsbook") or row.get("provider") or "RapidAPI").strip()
        norm = normalize_bookmaker_name(raw)
        mapping = {
            "bet365": "Bet365",
            "unibet": "Unibet",
            "betfair": "Betfair",
            "sbobet": "Sbobet",
            "pinnacle": "Pinnacle",
            "draftkings": "DraftKings",
            "fanduel": "FanDuel",
            "betmgm": "BetMGM",
            "1xbet": "1xBet",
        }
        return mapping.get(norm, raw or "RapidAPI")

    @staticmethod
    def _price(row: dict[str, Any]) -> float | None:
        for key in ("price", "odds", "decimal", "decimalOdds", "value"):
            value = row.get(key)
            try:
                if value not in (None, ""):
                    num = float(str(value).replace(",", "."))
                    if num > 1.0:
                        return num
            except Exception:
                continue
        return None

    @staticmethod
    def _point(row: dict[str, Any], fallback_text: str) -> float | None:
        for key in ("line", "point", "handicap"):
            try:
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", "."))
            except Exception:
                continue
        match = re.search(r"([-+]?\d+(?:[\.,]\d+)?)", fallback_text or "")
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except Exception:
                return None
        return None

    @staticmethod
    def _norm(value: Any) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    @classmethod
    def _team_like(cls, value: str, team: str) -> bool:
        v = cls._norm(value)
        t = cls._norm(team)
        return bool(v and t and (v == t or v in t or t in v))

    def _budget_exhausted(self, stats: dict[str, Any]) -> bool:
        if self.max_requests <= 0:
            return False
        if int(stats.get("requests", 0) or 0) >= self.max_requests:
            stats["budget_exhausted"] = True
            stats["last_error"] = "rapidapi_odds_budget_exhausted"
            return True
        return False

    @staticmethod
    def _provider_budget_exhausted(provider: dict[str, Any], provider_stats: dict[str, Any]) -> bool:
        limit = max(0, int(provider.get("max_requests") or 0))
        return limit > 0 and int(provider_stats.get("requests", 0) or 0) >= limit

    @staticmethod
    def _api_key(provider: dict[str, Any]) -> str:
        for name in provider.get("key_envs") or []:
            value = str(os.getenv(str(name)) or "").strip()
            if value:
                return value
        return ""

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "rapidapi_odds_bridge_offers.json"

    def _load_cache(self, matches: list[Match]) -> dict[str, list[Offer]] | None:
        try:
            payload = json.loads(self._cache_path().read_text(encoding="utf-8"))
            fetched_at = parse_datetime(payload.get("fetched_at"))
        except Exception:
            return None
        if datetime.now(UTC) - fetched_at.astimezone(UTC) > timedelta(minutes=self.cache_ttl_minutes):
            return None
        wanted = {m.match_key for m in matches}
        out: dict[str, list[Offer]] = {}
        for match_key, rows in (payload.get("offers") or {}).items():
            if match_key not in wanted or not isinstance(rows, list):
                continue
            offers: list[Offer] = []
            for row in rows:
                if isinstance(row, dict):
                    try:
                        offers.append(Offer(**row))
                    except Exception:
                        continue
            if offers:
                out[match_key] = offers
        return out or None

    def _write_cache(self, offers_by_match: dict[str, list[Offer]]) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": datetime.now(UTC).isoformat(), "offers": {k: [asdict(item) for item in v] for k, v in offers_by_match.items()}}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    @staticmethod
    def _prioritize_matches(matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        return sorted(matches, key=lambda m: (0 if getattr(m, "tier", "mid") == "top" else 1, abs((m.commence_time - now).total_seconds()), m.league_name.lower()))

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
