from __future__ import annotations

import os
import re
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.schemas import Match, MatchContext, Offer
from app.utils import clamp, normalize_bookmaker_name, parse_datetime, score_event_match

UTC = timezone.utc


class SportLogicProvider:
    """SportLogic adapter for the bot runtime.

    The old version of this file was only a tiny synchronous SDK wrapper.  The
    runner, however, expects providers to be instantiated with Settings and to
    expose async fetch_matches/fetch_offers/fetch_context methods.  This adapter
    keeps the simple SDK methods for manual probes, but also implements the bot
    provider contract with defensive parsing because SportLogic deployments can
    expose slightly different JSON envelopes.
    """

    BASE_URL = "https://api.sportlogic.io/v1"
    MAX_DAILY = 500

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.api_key = (
            getattr(settings, "sportlogic_api_key", None)
            or os.getenv("SPORTLOGIC_API_KEY")
            or os.getenv("SPORTLOGIC_KEY")
            or os.getenv("SPORTLOGIC_TOKEN")
        )
        self.base_url = str(
            getattr(settings, "sportlogic_base_url", None)
            or os.getenv("SPORTLOGIC_BASE_URL")
            or self.BASE_URL
        ).rstrip("/")
        self.timeout = float(
            getattr(settings, "sportlogic_timeout_seconds", None)
            or os.getenv("SPORTLOGIC_TIMEOUT_SECONDS")
            or 20.0
        )
        self.match_limit = max(
            1,
            int(float(
                getattr(settings, "sportlogic_match_limit", None)
                or os.getenv("SPORTLOGIC_MATCH_LIMIT")
                or 80
            )),
        )
        self.odds_match_limit = max(
            1,
            int(float(
                getattr(settings, "sportlogic_odds_match_limit", None)
                or os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT")
                or 40
            )),
        )
        self.max_requests_per_run = max(
            0,
            int(float(
                getattr(settings, "sportlogic_per_run_max", None)
                or os.getenv("SPORTLOGIC_PER_RUN_MAX")
                or 80
            )),
        )
        self.enabled = self._env_bool("ENABLE_SPORTLOGIC", True) and self._env_bool("SPORTLOGIC_ENABLED", True)
        self._requests = 0
        self._fixture_cache: list[dict[str, Any]] = []

    # ---------------------------------------------------------------------
    # Legacy probe methods kept for compatibility with manual scripts.
    # ---------------------------------------------------------------------
    def get_fixtures(self, date: str | None = None) -> Any:
        import requests

        params = {"date": date} if date else {}
        response = requests.get(
            f"{self.base_url}/football/fixtures",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_odds(self, fixture_id: int | str) -> Any:
        import requests

        response = requests.get(
            f"{self.base_url}/football/odds/{fixture_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_results(self, fixture_id: int | str) -> Any:
        import requests

        response = requests.get(
            f"{self.base_url}/football/results/{fixture_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------------
    # Bot provider contract.
    # ---------------------------------------------------------------------
    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats = self._stats("matches")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": []}
        if not self._ready(stats):
            return [], stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 3) or 3))
        fixtures: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for offset in range(days_ahead + 1):
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                date_key = (now + timedelta(days=offset)).date().isoformat()
                payload = await self._get_json(client, "/football/fixtures", {"date": date_key}, stats, preview)
                fixtures.extend(self._extract_list(payload))

        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        preview["sample_fixtures"] = fixtures[:3]

        matches: list[Match] = []
        seen: set[str] = set()
        horizon = now + timedelta(days=days_ahead)
        for row in fixtures:
            match = self._row_to_match(row)
            if match is None:
                stats["fixtures_skipped"] += 1
                continue
            commence = match.commence_time.astimezone(UTC)
            if commence < now - timedelta(hours=2) or commence > horizon:
                continue
            if match.match_key in seen:
                continue
            seen.add(match.match_key)
            matches.append(match)

        matches = self._prioritize_matches(matches)[: self.match_limit]
        stats["matches_built"] = len(matches)
        preview["sample_matches"] = [
            {
                "match_key": item.match_key,
                "league_name": item.league_name,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "commence_time": item.commence_time.isoformat(),
            }
            for item in matches[:8]
        ]
        return matches, stats, preview

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats = self._stats("offers")
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_odds": [], "errors": []}
        if not self._ready(stats):
            return {}, stats, preview

        soccer_matches = [m for m in matches if getattr(m, "sport_key", "") == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        fixtures = list(self._fixture_cache)
        if not fixtures:
            fixtures, fixture_stats, fixture_preview = await self._load_fixtures_for_matches(soccer_matches)
            self._merge_stats(stats, fixture_stats)
            preview["sample_fixtures"] = fixture_preview.get("sample_fixtures", [])[:3]
        else:
            preview["sample_fixtures"] = fixtures[:3]

        mapping = self._match_fixtures(soccer_matches, fixtures, stats)
        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        prioritized_items = list(mapping.values())[: self.odds_match_limit]

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for item in prioritized_items:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                event_id = str(item["event_id"] or "").strip()
                if not event_id:
                    continue
                payload = await self._fetch_odds_payload(client, event_id, stats, preview)
                rows = self._extract_odds_rows(payload)
                stats["odds_payload_rows"] += len(rows)
                if not rows:
                    stats["empty_odds_payloads"] += 1
                if rows and len(preview["sample_odds"]) < 3:
                    preview["sample_odds"].append(rows[0])
                parsed = self._parse_odds(rows, item["match"], event_id)
                if parsed:
                    offers_by_match[item["match"].match_key].extend(parsed)
                    stats["offers_parsed"] += len(parsed)

        stats["events_matched"] = len(mapping)
        stats["games_fetched"] = int(stats.get("fixtures_fetched", 0) or 0)
        self._write_debug_export(stats, preview)
        return {key: value for key, value in offers_by_match.items() if value}, stats, preview

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats = self._stats("context")
        preview: dict[str, Any] = {"sample_contexts": [], "errors": []}
        if not self._ready(stats):
            return {}, stats, preview

        soccer_matches = [m for m in matches if getattr(m, "sport_key", "") == "soccer"][: self.match_limit]
        if not soccer_matches:
            return {}, stats, preview

        fixtures = list(self._fixture_cache)
        if not fixtures:
            fixtures, fixture_stats, _ = await self._load_fixtures_for_matches(soccer_matches)
            self._merge_stats(stats, fixture_stats)

        mapping = self._match_fixtures(soccer_matches, fixtures, stats)
        contexts: dict[str, MatchContext] = {}
        for item in mapping.values():
            context = self._context_from_fixture(item["match"], item["row"])
            if context is None:
                continue
            contexts[item["match"].match_key] = context
            if len(preview["sample_contexts"]) < 8:
                preview["sample_contexts"].append({
                    "match_key": item["match"].match_key,
                    "expected_home": context.expected_home,
                    "expected_away": context.expected_away,
                    "confidence": context.confidence,
                })

        stats["contexts_built"] = len(contexts)
        return contexts, stats, preview

    def supports_match(self, match: Match) -> bool:
        return getattr(match, "sport_key", "") == "soccer"

    # ------------------------------------------------------------------
    # HTTP and parsing helpers.
    # ------------------------------------------------------------------
    async def _load_fixtures_for_matches(self, matches: list[Match]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        stats = self._stats("fixtures")
        preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}
        dates = sorted({m.commence_time.astimezone(UTC).date().isoformat() for m in matches})[:6]
        fixtures: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for date_key in dates:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                payload = await self._get_json(client, "/football/fixtures", {"date": date_key}, stats, preview)
                fixtures.extend(self._extract_list(payload))
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> Any | None:
        self._requests += 1
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or None)
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "request", exc)
            return None
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1600]
        if response.status_code in {401, 403}:
            stats["auth_error"] = True
        if response.status_code == 429:
            stats["rate_limited"] = True
        if response.status_code >= 400:
            stats["response_errors"] += 1
            return None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                keys = stats.setdefault("top_level_keys", [])
                for key in payload.keys():
                    if key not in keys and len(keys) < 25:
                        keys.append(key)
            return payload
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "json", exc)
            return None

    async def _fetch_odds_payload(
        self,
        client: httpx.AsyncClient,
        event_id: str,
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> Any | None:
        endpoints = [
            (f"/football/odds/{event_id}", {}),
            (f"/games/{event_id}/odds", {}),
            ("/odds", {"game_id": event_id}),
        ]
        for path, params in endpoints:
            if not self._budget_left():
                stats["budget_exhausted"] = True
                return None
            stats["odds_requests"] += 1
            payload = await self._get_json(client, path, params, stats, preview)
            rows = self._extract_odds_rows(payload)
            if rows:
                stats["odds_endpoint_used"] = path
                return payload
        return None

    def _write_debug_export(self, stats: dict[str, Any], preview: dict[str, Any]) -> None:
        try:
            path = Path(".data/exports/latest-sportlogic-debug.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "stats": {key: value for key, value in stats.items() if key != "last_body_preview"},
                "preview": self._sanitize(preview),
                "last_body_preview": str(stats.get("last_body_preview") or "")[:800],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            return

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                low = str(key).lower()
                if any(token in low for token in ("key", "token", "secret", "authorization", "apikey", "api_key")):
                    out[str(key)] = "***"
                else:
                    out[str(key)] = self._sanitize(item)
            return out
        if isinstance(value, list):
            return [self._sanitize(item) for item in value[:10]]
        if isinstance(value, str):
            return value[:500]
        return value

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if not self.api_key:
            return headers
        header_name = os.getenv("SPORTLOGIC_HEADER_NAME", "Authorization").strip() or "Authorization"
        if header_name.lower() == "authorization":
            scheme = os.getenv("SPORTLOGIC_AUTH_SCHEME", "Bearer").strip()
            headers["Authorization"] = f"{scheme} {self.api_key}".strip()
        else:
            headers[header_name] = str(self.api_key)
        return headers

    @staticmethod
    def _extract_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("data", "response", "results", "fixtures", "matches", "events", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = SportLogicProvider._extract_list(value)
                if nested:
                    return nested
        return []

    @staticmethod
    def _extract_odds_rows(payload: Any) -> list[dict[str, Any]]:
        rows = SportLogicProvider._extract_list(payload)
        if rows:
            return rows
        if isinstance(payload, dict):
            return [payload]
        return []

    def _row_to_match(self, row: dict[str, Any]) -> Match | None:
        home = self._team_name(row, "home")
        away = self._team_name(row, "away")
        if not home or not away:
            return None
        commence = self._fixture_datetime(row)
        if commence is None:
            return None
        league = self._league_name(row)
        event_id = self._event_id(row)
        return Match(
            source="sportlogic",
            source_event_id=event_id,
            sport_key="soccer",
            league_name=league,
            home_team=home,
            away_team=away,
            commence_time=commence,
            home_team_norm="",
            away_team_norm="",
            league_key=str(self._dig(row, "league", "id") or self._dig(row, "competition", "id") or ""),
            tier="low" if self._is_low_tier_text(league) else "mid",
            metadata={"sportlogic_fixture": row},
        )

    def _match_fixtures(self, matches: list[Match], fixtures: list[dict[str, Any]], stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        for row in fixtures:
            home = self._team_name(row, "home")
            away = self._team_name(row, "away")
            start = self._fixture_datetime(row)
            if not home or not away or start is None:
                continue
            league = self._league_name(row)
            best_match = None
            best_score = 0.0
            best_quality = None
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
                    exact_tolerance_hours=exact_tol,
                    fuzzy_tolerance_hours=fuzzy_tol,
                )
                if score > best_score:
                    best_score = score
                    best_match = match
                    best_quality = quality
            if best_match is None or best_score < 50:
                continue
            mapping[best_match.match_key] = {
                "match": best_match,
                "row": row,
                "event_id": self._event_id(row),
                "score": best_score,
                "quality": best_quality,
            }
            if best_quality == "exact":
                stats["matched_exact"] += 1
            elif best_quality == "loose":
                stats["matched_loose"] += 1
            elif best_quality == "fuzzy":
                stats["matched_fuzzy"] += 1
        return mapping

    def _parse_odds(self, rows: list[dict[str, Any]], match: Match, event_id: str) -> list[Offer]:
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()
        configured_books = getattr(self.settings, "sportlogic_bookmakers", None)
        if configured_books is None:
            configured_books = os.getenv("SPORTLOGIC_BOOKMAKERS", "")
        if isinstance(configured_books, str):
            book_items = [item.strip() for item in configured_books.split(",") if item.strip()]
        else:
            book_items = [str(item).strip() for item in (configured_books or []) if str(item).strip()]
        allowed = {self._canonical_bookmaker(item) for item in book_items}
        allowed.discard("")

        def add(book: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, market_name: str = "") -> None:
            odds = self._float(price)
            if odds is None or odds <= 1.0:
                return
            bookmaker = self._canonical_bookmaker(book or "SportLogic")
            if allowed and bookmaker not in allowed:
                return
            key = (bookmaker, family, selection, point)
            if key in seen:
                return
            seen.add(key)
            offers.append(Offer(
                source="sportlogic",
                bookmaker=bookmaker,
                family=family,  # type: ignore[arg-type]
                selection=selection,
                price=float(odds),
                point=point,
                team_side=team_side,
                market_name=market_name or family,
                market_key=family,
                source_event_id=event_id,
                metadata={"sportlogic_event_id": event_id},
            ))

        for row in rows:
            # Shape A: bookmakers -> markets -> outcomes
            bookmakers = row.get("bookmakers") if isinstance(row, dict) else None
            if isinstance(bookmakers, list):
                for bookmaker_payload in [x for x in bookmakers if isinstance(x, dict)]:
                    book = str(bookmaker_payload.get("name") or bookmaker_payload.get("bookmaker") or bookmaker_payload.get("title") or "SportLogic")
                    for market in self._market_rows(bookmaker_payload):
                        self._parse_market(market, match, book, add)
                continue

            # Shape B: markets at top level
            for market in self._market_rows(row):
                book = str(row.get("bookmaker") or row.get("bookmaker_name") or row.get("book") or "SportLogic")
                self._parse_market(market, match, book, add)

            # Shape C: flattened odds fields
            book = str(row.get("bookmaker") or row.get("bookmaker_name") or row.get("book") or "SportLogic")
            add(book, "h2h", match.home_team, row.get("home") or row.get("home_odds") or row.get("odd_1"), team_side="home")
            add(book, "h2h", "Draw", row.get("draw") or row.get("draw_odds") or row.get("odd_x"))
            add(book, "h2h", match.away_team, row.get("away") or row.get("away_odds") or row.get("odd_2"), team_side="away")
            add(book, "btts", "Yes", row.get("btts_yes") or row.get("both_teams_to_score_yes"))
            add(book, "btts", "No", row.get("btts_no") or row.get("both_teams_to_score_no"))
            for key, value in row.items():
                low = str(key).lower()
                total_match = re.search(r"(over|under|[ou])[_\s-]*(\d+(?:\.\d+)?)", low)
                if total_match:
                    sel = total_match.group(1)
                    point = self._float(total_match.group(2))
                    add(book, "totals", "Over" if sel in {"over", "o"} else "Under", value, point)
        return offers

    @staticmethod
    def _market_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for key in ("markets", "odds", "bets"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([row for row in value if isinstance(row, dict)])
            elif isinstance(value, dict):
                for name, nested in value.items():
                    if isinstance(nested, dict):
                        item = dict(nested)
                        item.setdefault("name", name)
                        rows.append(item)
                    elif isinstance(nested, list):
                        for entry in nested:
                            if isinstance(entry, dict):
                                item = dict(entry)
                                item.setdefault("name", name)
                                rows.append(item)
        return rows

    def _parse_market(self, market: dict[str, Any], match: Match, bookmaker: str, add: Any) -> None:
        raw_name = str(market.get("key") or market.get("name") or market.get("market") or market.get("label") or "").lower()
        outcomes = market.get("outcomes") or market.get("values") or market.get("selections") or market.get("odds")
        if isinstance(outcomes, dict):
            outcomes = [{"name": key, "price": value} for key, value in outcomes.items()]
        if not isinstance(outcomes, list):
            outcomes = []

        family = self._market_family(raw_name)
        for outcome in [x for x in outcomes if isinstance(x, dict)]:
            name = str(outcome.get("name") or outcome.get("selection") or outcome.get("label") or outcome.get("team") or "").strip()
            price = outcome.get("price") or outcome.get("odds") or outcome.get("value") or outcome.get("decimal")
            point = self._float(outcome.get("point") or outcome.get("line") or outcome.get("handicap"))
            low = name.lower()

            if family == "h2h":
                if low in {"home", "1", "home_team"}:
                    add(bookmaker, "h2h", match.home_team, price, team_side="home", market_name=raw_name)
                elif low in {"draw", "x", "tie"}:
                    add(bookmaker, "h2h", "Draw", price, market_name=raw_name)
                elif low in {"away", "2", "away_team"}:
                    add(bookmaker, "h2h", match.away_team, price, team_side="away", market_name=raw_name)
                else:
                    side = "home" if name == match.home_team else "away" if name == match.away_team else None
                    add(bookmaker, "h2h", name, price, team_side=side, market_name=raw_name)
            elif family == "totals":
                if "under" in low or low.startswith("u"):
                    add(bookmaker, "totals", "Under", price, point, market_name=raw_name)
                elif "over" in low or low.startswith("o"):
                    add(bookmaker, "totals", "Over", price, point, market_name=raw_name)
            elif family == "spreads":
                side = "home" if low in {"home", "1"} or name == match.home_team else "away" if low in {"away", "2"} or name == match.away_team else None
                selection = match.home_team if side == "home" else match.away_team if side == "away" else name
                if point == 0:
                    add(bookmaker, "dnb", selection, price, 0.0, side, market_name=raw_name)
                else:
                    add(bookmaker, "spreads", selection, price, point, side, market_name=raw_name)
            elif family == "btts":
                if "yes" in low:
                    add(bookmaker, "btts", "Yes", price, market_name=raw_name)
                elif "no" in low:
                    add(bookmaker, "btts", "No", price, market_name=raw_name)

    @staticmethod
    def _market_family(raw_name: str) -> str:
        text = raw_name.lower()
        if any(token in text for token in ("total", "over/under", "goals over")):
            return "totals"
        if any(token in text for token in ("spread", "handicap", "asian handicap")):
            return "spreads"
        if "both" in text and "score" in text or "btts" in text:
            return "btts"
        return "h2h"

    def _context_from_fixture(self, match: Match, row: dict[str, Any]) -> MatchContext | None:
        home_prob = self._percent(
            self._dig(row, "predictions", "percent", "home")
            or self._dig(row, "prediction", "home")
            or row.get("home_probability")
        )
        away_prob = self._percent(
            self._dig(row, "predictions", "percent", "away")
            or self._dig(row, "prediction", "away")
            or row.get("away_probability")
        )
        expected_home = self._float(
            row.get("home_xg")
            or row.get("expected_home")
            or self._dig(row, "expected_goals", "home")
            or self._dig(row, "xg", "home")
        )
        expected_away = self._float(
            row.get("away_xg")
            or row.get("expected_away")
            or self._dig(row, "expected_goals", "away")
            or self._dig(row, "xg", "away")
        )
        if home_prob is None and away_prob is None and expected_home is None and expected_away is None:
            return None
        confidence = 54.0
        if expected_home is not None and expected_away is not None:
            confidence += 3.0
        if home_prob is not None and away_prob is not None:
            confidence += 2.0
        return MatchContext(
            source="sportlogic",
            payload={"fixture": row},
            expected_home=round(expected_home, 3) if expected_home is not None else None,
            expected_away=round(expected_away, 3) if expected_away is not None else None,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=clamp(confidence, 52.0, 62.0),
            details={"sportlogic_context": True},
        )

    def _stats(self, mode: str) -> dict[str, Any]:
        return {
            "enabled": self.enabled and bool(self.api_key),
            "mode": mode,
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "max_requests_per_run": self.max_requests_per_run,
            "budget_exhausted": False,
            "response_errors": 0,
            "auth_error": False,
            "rate_limited": False,
            "fixtures_fetched": 0,
            "games_fetched": 0,
            "fixtures_skipped": 0,
            "matches_built": 0,
            "events_matched": 0,
            "odds_requests": 0,
            "odds_payload_rows": 0,
            "offers_parsed": 0,
            "empty_odds_payloads": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "http_statuses": [],
            "top_level_keys": [],
            "last_body_preview": None,
        }

    def _ready(self, stats: dict[str, Any]) -> bool:
        if not self.enabled:
            stats["enabled"] = False
            stats["reason"] = "disabled_by_config"
            return False
        if not self.api_key:
            stats["enabled"] = False
            stats["reason"] = "missing_api_key"
            return False
        return True

    def _budget_left(self) -> bool:
        return self.max_requests_per_run <= 0 or self._requests < self.max_requests_per_run

    @staticmethod
    def _merge_stats(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if key == "http_statuses":
                target.setdefault(key, []).extend(value or [])
            elif isinstance(value, (int, float)) and isinstance(target.get(key), (int, float)):
                target[key] += value
            elif key not in target:
                target[key] = value

    @staticmethod
    def _team_name(row: dict[str, Any], side: str) -> str:
        direct_keys = [f"{side}_team", f"{side}Team", side, f"{side}_name"]
        for key in direct_keys:
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                name = value.get("name") or value.get("team") or value.get("title")
                if name:
                    return str(name).strip()
        teams = row.get("teams")
        if isinstance(teams, dict):
            value = teams.get(side)
            if isinstance(value, dict):
                return str(value.get("name") or value.get("team") or "").strip()
            if isinstance(value, str):
                return value.strip()
        return ""

    @staticmethod
    def _league_name(row: dict[str, Any]) -> str:
        for key in ("league", "competition", "tournament"):
            value = row.get(key)
            if isinstance(value, dict):
                name = value.get("name") or value.get("title")
                if name:
                    return str(name).strip()
            elif isinstance(value, str) and value.strip():
                return value.strip()
        return str(row.get("league_name") or row.get("competition_name") or "Unknown").strip()

    @staticmethod
    def _fixture_datetime(row: dict[str, Any]) -> datetime | None:
        candidates = [
            row.get("commence_time"),
            row.get("start_time"),
            row.get("kickoff"),
            row.get("date_time"),
            row.get("datetime"),
            row.get("timestamp"),
            SportLogicProvider._dig(row, "fixture", "date"),
        ]
        date_value = row.get("date") or row.get("match_date")
        time_value = row.get("time") or row.get("match_time")
        if date_value:
            candidates.append(f"{date_value}T{time_value or '12:00:00'}")
        for value in candidates:
            if value in (None, ""):
                continue
            try:
                if isinstance(value, (int, float)) or str(value).isdigit():
                    raw = float(value)
                    if raw > 10_000_000_000:
                        raw /= 1000.0
                    return datetime.fromtimestamp(raw, tz=UTC)
                text = str(value).strip().replace(" ", "T")
                if "T" in text and not text.endswith("Z") and "+" not in text:
                    text += "+00:00"
                return parse_datetime(text)
            except Exception:
                continue
        return None

    @staticmethod
    def _event_id(row: dict[str, Any]) -> str:
        for key in ("id", "fixture_id", "event_id", "match_id", "game_id"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        nested = SportLogicProvider._dig(row, "fixture", "id")
        return str(nested or "")

    @staticmethod
    def _canonical_bookmaker(name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        norm = normalize_bookmaker_name(text)
        mapping = {
            "bet365": "Bet365",
            "unibet": "Unibet",
            "pinnacle": "Pinnacle",
            "betfair": "Betfair",
            "bwin": "Bwin",
            "1xbet": "1xBet",
        }
        return mapping.get(norm, text)

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value in (None, "", "null"):
                return None
            text = str(value).strip().replace("%", "").replace(",", ".")
            return float(text)
        except Exception:
            return None

    @staticmethod
    def _percent(value: Any) -> float | None:
        number = SportLogicProvider._float(value)
        if number is None:
            return None
        if number > 1.0:
            number /= 100.0
        return clamp(number, 0.01, 0.98)

    @staticmethod
    def _dig(payload: Any, *path: str) -> Any:
        current = payload
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _is_low_tier_text(text: str) -> bool:
        low = str(text or "").lower()
        return any(token in low for token in ("women", "youth", "u19", "u21", "reserve", "friendly"))

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or raw == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _preview_error(preview: dict[str, Any], source: str, exc: Exception) -> None:
        errors = preview.setdefault("errors", [])
        if isinstance(errors, list) and len(errors) < 8:
            errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})

    @staticmethod
    def _prioritize_matches(matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)

        def key(match: Match) -> tuple[int, float, str]:
            tier = getattr(match, "tier", "mid")
            tier_rank = 0 if tier == "top" else 1 if tier == "mid" else 2
            kickoff_distance = abs((match.commence_time.astimezone(UTC) - now).total_seconds()) / 3600.0
            return tier_rank, kickoff_distance, match.league_name.lower()

        return sorted(matches, key=key)
