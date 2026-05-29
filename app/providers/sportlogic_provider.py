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

    BASE_URL = "https://api.sportlogic.io/api/v1"
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
                os.getenv("SPORTLOGIC_REQUEST_BUDGET_GRANTED")
                or os.getenv("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN")
                or os.getenv("SPORTLOGIC_REQUESTS_MAX_PER_RUN")
                or os.getenv("SPORTLOGIC_PER_RUN_MAX")
                or getattr(settings, "sportlogic_per_run_max", None)
                or 6
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
            f"{self.base_url}/games",
            headers=self._headers(),
            params={"date_from": date, "date_to": date, "per_page": 100} if date else {},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_odds(self, fixture_id: int | str) -> Any:
        import requests

        response = requests.get(
            f"{self.base_url}/games/{fixture_id}/odds",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_results(self, fixture_id: int | str) -> Any:
        import requests

        response = requests.get(
            f"{self.base_url}/outcomes/{fixture_id}",
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
                for params in self._game_query_params(date_key):
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await self._get_json(client, "/games", params, stats, preview)
                    rows = self._extract_list(payload)
                    stats.setdefault("games_query_variants", []).append({"params": params, "rows": len(rows)})
                    if rows:
                        fixtures.extend(rows)
                        break

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
                stats["rows_before_parse"] += len(rows)
                stats["odds_payload_rows"] += len(rows)
                if not rows:
                    stats["empty_odds_payloads"] += 1
                if rows and len(preview["sample_odds"]) < 3:
                    preview["sample_odds"].append(rows[0])
                if rows:
                    self._write_odds_sample(event_id, item["match"], payload, rows[0])
                parsed = self._parse_odds(rows, item["match"], event_id, stats)
                if parsed:
                    offers_by_match[item["match"].match_key].extend(parsed)
                    stats["offers_parsed"] += len(parsed)

        if self._env_bool("SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED", True):
            min_targeted = max(1, int(float(os.getenv("SPORTLOGIC_TARGETED_MIN_OFFER_MATCHES") or 1)))
            if len(offers_by_match) < min_targeted and self._budget_left():
                targeted_offers = await self._fetch_active_odds_targeted(soccer_matches, stats, preview)
                for match_key, offers in targeted_offers.items():
                    if offers:
                        offers_by_match[match_key].extend(offers)
                        stats["offers_parsed"] += len(offers)

        stats["events_matched"] = max(len(mapping), int(stats.get("active_odds_targeted_matches", 0) or 0))
        stats["games_fetched"] = int(stats.get("fixtures_fetched", 0) or 0)
        reject_reasons = stats.get("parse_reject_reasons") if isinstance(stats.get("parse_reject_reasons"), dict) else {}
        if int(stats.get("rows_before_parse", 0) or 0) > 0 and int(stats.get("offers_parsed", 0) or 0) <= 0:
            if reject_reasons and "missing_or_invalid_price" in reject_reasons:
                stats["odds_disabled_reason"] = "price_missing_in_payload"
            else:
                stats["odds_disabled_reason"] = "parser_shape_unmatched"
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
                for params in self._game_query_params(date_key):
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await self._get_json(client, "/games", params, stats, preview)
                    rows = self._extract_list(payload)
                    stats.setdefault("games_query_variants", []).append({"params": params, "rows": len(rows)})
                    if rows:
                        fixtures.extend(rows)
                        break
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    def _game_query_params(self, date_key: str) -> list[dict[str, Any]]:
        """Cheap documented /games probes for a single UTC date.

        SportLogic docs show `date_from` + `status=scheduled` as the first
        fixture request and list `date_to` as optional.  Use at most two
        variants in runtime: the scheduled form first, then a no-status fallback.
        The old 8-variant probing burned free-plan quota without adding signal.
        """
        try:
            day = datetime.fromisoformat(str(date_key)).date()
            next_day = (datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)).date().isoformat()
        except Exception:
            next_day = str(date_key)
        per_page = max(5, min(100, int(float(os.getenv("SPORTLOGIC_GAMES_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE") or 100))))
        variants = [
            {"date_from": date_key, "date_to": next_day, "status": "scheduled", "per_page": per_page},
            {"date_from": date_key, "date_to": next_day, "per_page": per_page},
        ]
        if self._env_bool("SPORTLOGIC_GAMES_DATE_FROM_ONLY_FALLBACK", True):
            variants.append({"date_from": date_key, "status": "scheduled", "per_page": per_page})
        return variants

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
            stats["diagnosis"] = "sportlogic_rate_limited"
            self._write_rate_limit_state(response)
            body_text = str(response.text or "")
            if "RATE_LIMIT_EXCEEDED" in body_text or "Daily limit" in body_text or "daily limit" in body_text.lower():
                stats["daily_limit_exceeded"] = True
                stats["diagnosis"] = "sportlogic_daily_limit_exceeded"
                self._write_daily_limit_state(response)
            if self._env_bool("SPORTLOGIC_STOP_RUN_ON_429", True):
                self._rate_limited_this_run = True
                if self.max_requests_per_run > 0:
                    self._requests = max(self._requests, self.max_requests_per_run)
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

    async def _fetch_active_odds_targeted(
        self,
        matches: list[Match],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, list[Offer]]:
        """Use one `/odds?is_active=true` page as targeted confirmation.

        The documented `/odds` response usually contains `game_id` but not the
        embedded teams/start time.  The previous implementation skipped those
        rows as unmatchable.  This version groups rows by `game_id`, fetches a
        very small number of `/games/{id}` details, matches only those details
        against current bot matches, and parses the grouped odds rows directly.
        """
        if not matches or not self._budget_left():
            return {}
        requested_dates = {
            dt.astimezone(UTC).date().isoformat()
            for dt in (self._safe_match_datetime(match) for match in matches)
            if dt is not None
        }
        per_page = max(5, min(100, int(float(os.getenv("SPORTLOGIC_ACTIVE_ODDS_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE") or 100))))
        params: dict[str, Any] = {"is_active": "true", "per_page": per_page}
        market_id = str(os.getenv("SPORTLOGIC_ACTIVE_ODDS_MARKET_ID") or "").strip()
        if market_id:
            params["market_id"] = market_id

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            payload = await self._get_json(client, "/odds", params, stats, preview)
            rows = self._extract_odds_rows(payload)
            stats["active_odds_rows_seen"] = int(stats.get("active_odds_rows_seen") or 0) + len(rows)
            preview.setdefault("query_variants_used", []).append({
                "scope": "active_odds_targeted_confirmation",
                "params": params,
                "rows": len(rows),
            })
            if not rows:
                return {}

            grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            embedded_games: dict[str, dict[str, Any]] = {}
            stale_embedded_game_ids: set[str] = set()
            rows_without_game = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if self._is_suspended(row):
                    stats["active_odds_suspended_rows_skipped"] = int(stats.get("active_odds_suspended_rows_skipped") or 0) + 1
                    continue
                game_id = str(self._game_id(row) or "").strip()
                if not game_id:
                    rows_without_game += 1
                    continue
                grouped_rows[game_id].append(row)
                embedded = self._fixture_from_odds_row(row)
                if embedded is not None:
                    embedded_date = self._fixture_date_key(embedded)
                    if requested_dates and embedded_date and embedded_date not in requested_dates:
                        stats["active_odds_embedded_games_outside_requested_dates"] = int(stats.get("active_odds_embedded_games_outside_requested_dates") or 0) + 1
                        stale_embedded_game_ids.add(game_id)
                    else:
                        embedded_games.setdefault(game_id, embedded)

            stats["active_odds_game_ids_seen"] = len(grouped_rows)
            stats["active_odds_rows_without_game_id"] = rows_without_game
            if not grouped_rows:
                return {}

            # Prefer game ids whose rows are relevant to publishable families and
            # have more bookmaker depth.  Without embedded fixture data this is the
            # cheapest useful ordering before spending detail requests.
            def group_rank(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int, int]:
                _gid, group = item
                families = {self._market_family(self._market_name_from_row(r)) for r in group if isinstance(r, dict)}
                books = {self._canonical_bookmaker(self._bookmaker_name(r)) for r in group if isinstance(r, dict)}
                useful = int(bool(families & {"totals", "spreads", "btts", "h2h"}))
                return (useful, len(books), len(group))

            detail_limit = max(0, int(float(os.getenv("SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT") or os.getenv("SPORTLOGIC_ACTIVE_ODDS_GAME_DETAIL_LIMIT") or 4)))
            candidate_games: dict[str, dict[str, Any]] = {}
            for game_id, group in sorted(grouped_rows.items(), key=group_rank, reverse=True):
                if game_id in embedded_games:
                    candidate_games[game_id] = embedded_games[game_id]
                    stats["active_odds_embedded_games_used"] = int(stats.get("active_odds_embedded_games_used") or 0) + 1
                    continue
                if game_id in stale_embedded_game_ids:
                    stats["active_odds_stale_embedded_game_ids_skipped"] = int(stats.get("active_odds_stale_embedded_game_ids_skipped") or 0) + 1
                    continue
                if detail_limit <= 0:
                    stats["active_odds_game_detail_limit_zero"] = True
                    continue
                if int(stats.get("active_odds_game_detail_requests") or 0) >= detail_limit:
                    stats["active_odds_game_detail_limit_reached"] = True
                    break
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                detail = await self._get_json(client, f"/games/{game_id}", {}, stats, preview)
                stats["active_odds_game_detail_requests"] = int(stats.get("active_odds_game_detail_requests") or 0) + 1
                game_row = self._extract_single_object(detail)
                if game_row is None:
                    stats["active_odds_game_detail_empty"] = int(stats.get("active_odds_game_detail_empty") or 0) + 1
                    continue
                detail_date = self._fixture_date_key(game_row)
                if requested_dates and detail_date and detail_date not in requested_dates:
                    stats["active_odds_game_detail_outside_requested_dates"] = int(stats.get("active_odds_game_detail_outside_requested_dates") or 0) + 1
                    continue
                candidate_games[game_id] = game_row

        match_by_key: dict[str, Match] = {m.match_key: m for m in matches}
        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        seen_game_matches: set[str] = set()
        for game_id, fixture_row in candidate_games.items():
            best = self._best_match_for_fixture_row(matches, fixture_row, stats)
            if best is None:
                stats["active_odds_detail_unmatched_games"] = int(stats.get("active_odds_detail_unmatched_games") or 0) + 1
                continue
            if best.match_key in seen_game_matches:
                continue
            seen_game_matches.add(best.match_key)
            parsed = self._parse_odds(grouped_rows.get(game_id, []), best, game_id, stats)
            if parsed:
                offers_by_match[best.match_key].extend(parsed)
                stats["active_odds_targeted_offers_parsed"] = int(stats.get("active_odds_targeted_offers_parsed") or 0) + len(parsed)

        stats["active_odds_targeted_matches"] = len(offers_by_match)
        if not offers_by_match and int(stats.get("active_odds_rows_seen") or 0) > 0:
            outside = int(stats.get("active_odds_embedded_games_outside_requested_dates") or 0) + int(stats.get("active_odds_game_detail_outside_requested_dates") or 0)
            if outside > 0 and int(stats.get("active_odds_targeted_matches") or 0) <= 0:
                stats["active_odds_stale_only"] = True
                stats["diagnosis"] = "active_odds_stale_only_no_current_fixture"
        if offers_by_match:
            stats["active_odds_targeted_enabled"] = True
            preview["sample_active_odds_targeted_matches"] = list(offers_by_match.keys())[:8]
        return {key: value for key, value in offers_by_match.items() if value}

    @staticmethod
    def _safe_match_datetime(match: Match) -> datetime | None:
        try:
            dt = getattr(match, "commence_time", None)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
        except Exception:
            return None
        return None

    def _fixture_date_key(self, row: dict[str, Any]) -> str:
        try:
            dt = self._fixture_datetime(row)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC).date().isoformat()
        except Exception:
            return ""
        return ""

    def _fixture_from_odds_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("game", "fixture", "event", "match"):
            value = row.get(key)
            if isinstance(value, dict):
                merged = dict(value)
                # Preserve ids from the odds row when the embedded game omits them.
                for id_key in ("game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId"):
                    if id_key not in merged and row.get(id_key) not in (None, ""):
                        merged[id_key] = row.get(id_key)
                return merged
        if self._team_name(row, "home") and self._team_name(row, "away") and self._fixture_datetime(row) is not None:
            return row
        return None

    def _best_match_for_fixture_row(self, matches: list[Match], row: dict[str, Any], stats: dict[str, Any]) -> Match | None:
        home = self._team_name(row, "home")
        away = self._team_name(row, "away")
        start = self._fixture_datetime(row)
        if not home or not away or start is None:
            stats["active_odds_unmatchable_fixture_rows"] = int(stats.get("active_odds_unmatchable_fixture_rows") or 0) + 1
            return None
        league = self._league_name(row)
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        best_match: Match | None = None
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
        min_score = float(os.getenv("SPORTLOGIC_ACTIVE_ODDS_MATCH_MIN_SCORE") or 50)
        if best_match is None or best_score < min_score:
            stats["active_odds_unmatched_rows"] = int(stats.get("active_odds_unmatched_rows") or 0) + 1
            return None
        stats["active_odds_matched_rows"] = int(stats.get("active_odds_matched_rows") or 0) + 1
        if best_quality == "exact":
            stats["active_odds_matched_exact"] = int(stats.get("active_odds_matched_exact") or 0) + 1
        elif best_quality == "loose":
            stats["active_odds_matched_loose"] = int(stats.get("active_odds_matched_loose") or 0) + 1
        elif best_quality == "fuzzy":
            stats["active_odds_matched_fuzzy"] = int(stats.get("active_odds_matched_fuzzy") or 0) + 1
        return best_match

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

    def _write_odds_sample(self, event_id: str, match: Match, payload: Any, row: dict[str, Any]) -> None:
        try:
            path = Path(".data/exports/latest-sportlogic-odds-sample.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            sample = {
                "event_id": event_id,
                "match_key": match.match_key,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "row": self._sanitize(row),
                "payload_top_level": self._sanitize(payload if isinstance(payload, dict) else {}),
            }
            path.write_text(json.dumps(sample, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
        header_name = os.getenv("SPORTLOGIC_HEADER_NAME", "X-API-Key").strip() or "X-API-Key"
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
            data = payload.get("data")
            if isinstance(data, dict) and SportLogicProvider._looks_like_odds_row(data):
                return [data]
            if SportLogicProvider._looks_like_odds_row(payload):
                return [payload]
        return []

    @staticmethod
    def _extract_single_object(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
            if any(key in payload for key in ("id", "home_team", "away_team", "start_time", "game_id")):
                return payload
        rows = SportLogicProvider._extract_list(payload)
        return rows[0] if rows else None

    def _row_to_match(self, row: dict[str, Any]) -> Match | None:
        home = self._team_name(row, "home")
        away = self._team_name(row, "away")
        if not home or not away:
            return None
        commence = self._fixture_datetime(row)
        if commence is None:
            return None
        league = self._league_name(row)
        game_id = self._game_id(row)
        return Match(
            source="sportlogic",
            source_event_id=game_id,
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
            game_id = self._game_id(row)
            if not game_id:
                stats["missing_game_id"] = int(stats.get("missing_game_id") or 0) + 1
                continue
            mapping[best_match.match_key] = {
                "match": best_match,
                "row": row,
                "event_id": game_id,
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

    def _parse_odds(self, rows: list[dict[str, Any]], match: Match, event_id: str, stats: dict[str, Any] | None = None) -> list[Offer]:
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

        def reject(reason: str) -> None:
            if stats is None:
                return
            reasons = stats.setdefault("parse_reject_reasons", {})
            if isinstance(reasons, dict):
                reasons[reason] = int(reasons.get(reason) or 0) + 1

        def add(book: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, market_name: str = "") -> bool:
            odds = self._float(price)
            if odds is None or odds <= 1.0:
                reject("missing_or_invalid_price")
                return False
            bookmaker = self._canonical_bookmaker(book or "SportLogic")
            if allowed and bookmaker not in allowed:
                reject("bookmaker_not_allowed")
                return False
            key = (bookmaker, family, selection, point)
            if key in seen:
                reject("duplicate_offer")
                return False
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
            return True

        for row in rows:
            self._record_odds_shape(row, stats)
            # Shape A: bookmakers -> markets -> outcomes
            bookmakers = row.get("bookmakers") if isinstance(row, dict) else None
            if isinstance(bookmakers, list):
                for bookmaker_payload in [x for x in bookmakers if isinstance(x, dict)]:
                    book = self._bookmaker_name(bookmaker_payload)
                    for market in self._market_rows(bookmaker_payload):
                        self._parse_market(market, match, book, add)
                continue

            # Shape B: markets at top level
            for market in self._market_rows(row):
                book = self._bookmaker_name(row)
                self._parse_market(market, match, book, add)

            # Shape C: flattened odds fields
            book = self._bookmaker_name(row)
            if self._is_suspended(row):
                reject("suspended_odds_row")
                continue
            self._parse_flat_odds_row(row, match, book, add, reject)
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
    def _record_seen(stats: dict[str, Any] | None, bucket: str, value: Any) -> None:
        if stats is None:
            return
        text = str(value or "").strip()
        if not text:
            return
        seen = stats.setdefault(bucket, [])
        if isinstance(seen, list) and text not in seen and len(seen) < 40:
            seen.append(text[:80])

    def _record_odds_shape(self, row: dict[str, Any], stats: dict[str, Any] | None) -> None:
        if stats is None or not isinstance(row, dict):
            return
        market_fields = {"market", "market_name", "market_key", "key", "name", "label", "type"}
        option_fields = {"outcome", "selection", "label", "option", "option_name", "name", "team"}
        price_fields = {"price", "decimal_odds", "value", "odd", "odds", "decimal", "option_price"}
        point_fields = {"total", "handicap", "line", "points", "point", "option_value"}
        for key, value in row.items():
            low = str(key).lower()
            if low in market_fields:
                self._record_seen(stats, "market_keys_seen", f"{low}={value}" if not isinstance(value, (dict, list)) else low)
            if low in option_fields:
                self._record_seen(stats, "option_keys_seen", f"{low}={value}" if not isinstance(value, (dict, list)) else low)
            if low in price_fields:
                self._record_seen(stats, "price_keys_seen", low)
            if low in point_fields:
                self._record_seen(stats, "line_keys_seen", f"{low}={value}" if not isinstance(value, (dict, list)) else low)
        for market in self._market_rows(row):
            for key, value in market.items():
                low = str(key).lower()
                if low in market_fields:
                    self._record_seen(stats, "market_keys_seen", f"{low}={value}" if not isinstance(value, (dict, list)) else low)
            outcomes = market.get("outcomes") or market.get("values") or market.get("selections") or market.get("options") or market.get("odds")
            if isinstance(outcomes, dict):
                outcomes = [{"name": key, "price": value} for key, value in outcomes.items()]
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    if isinstance(outcome, dict):
                        for key, value in outcome.items():
                            low = str(key).lower()
                            if low in option_fields:
                                self._record_seen(stats, "option_keys_seen", f"{low}={value}" if not isinstance(value, (dict, list)) else low)
                            if low in price_fields:
                                self._record_seen(stats, "price_keys_seen", low)

    def _parse_flat_odds_row(self, row: dict[str, Any], match: Match, book: str, add: Any, reject: Any) -> None:
        market_name = self._market_name_from_row(row)
        selection_name = str(row.get("outcome") or row.get("selection") or row.get("label") or row.get("option") or row.get("option_name") or row.get("name") or "").strip()
        price = row.get("price") or row.get("decimal_odds") or row.get("value") or row.get("odd") or row.get("odds") or row.get("decimal") or row.get("option_price")
        point = self._float(row.get("total") or row.get("handicap") or row.get("line") or row.get("points") or row.get("point") or row.get("option_value"))
        if not market_name and not selection_name and price in (None, ""):
            return
        if price in (None, ""):
            reject("missing_price")
            return
        if not market_name and not selection_name:
            reject("missing_market_and_selection")
            return

        family = self._market_family(market_name or selection_name)
        low = selection_name.lower()
        market_low = market_name.lower()
        if family == "h2h":
            if low in {"home", "1", "home_team"} or selection_name == match.home_team:
                add(book, "h2h", match.home_team, price, team_side="home", market_name=market_name)
            elif low in {"draw", "x", "tie"}:
                add(book, "h2h", "Draw", price, market_name=market_name)
            elif low in {"away", "2", "away_team"} or selection_name == match.away_team:
                add(book, "h2h", match.away_team, price, team_side="away", market_name=market_name)
            else:
                reject("unknown_h2h_selection")
        elif family == "totals":
            combined = f"{market_low} {low}"
            if point is None:
                match_obj = re.search(r"(\d+(?:\.\d+)?)", combined)
                point = self._float(match_obj.group(1)) if match_obj else None
            if low.startswith("o") or low in {"over", "o"}:
                add(book, "totals", "Over", price, point, market_name=market_name)
            elif low.startswith("u") or low in {"under", "u"}:
                add(book, "totals", "Under", price, point, market_name=market_name)
            elif "over" in combined and "under" not in low:
                add(book, "totals", "Over", price, point, market_name=market_name)
            elif "under" in combined:
                add(book, "totals", "Under", price, point, market_name=market_name)
            else:
                reject("unknown_total_selection")
        elif family == "spreads":
            side = "home" if low in {"home", "1"} or selection_name == match.home_team else "away" if low in {"away", "2"} or selection_name == match.away_team else None
            if side is None:
                reject("unknown_spread_side")
                return
            selection = match.home_team if side == "home" else match.away_team
            if point == 0:
                add(book, "dnb", selection, price, 0.0, side, market_name=market_name)
            else:
                add(book, "spreads", selection, price, point, side, market_name=market_name)
        elif family == "btts":
            if "yes" in low:
                add(book, "btts", "Yes", price, market_name=market_name)
            elif "no" in low:
                add(book, "btts", "No", price, market_name=market_name)
            else:
                reject("unknown_btts_selection")

    @staticmethod
    def _market_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for key in ("markets", "odds", "bets", "bookmaker_markets", "market_odds"):
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
        raw_name = str(market.get("key") or market.get("name") or market.get("market") or market.get("market_name") or market.get("market_key") or market.get("label") or "").lower()
        outcomes = market.get("outcomes") or market.get("values") or market.get("selections") or market.get("options") or market.get("odds")
        if isinstance(outcomes, dict):
            outcomes = [{"name": key, "price": value} for key, value in outcomes.items()]
        if not isinstance(outcomes, list):
            outcomes = []

        family = self._market_family(raw_name)
        for outcome in [x for x in outcomes if isinstance(x, dict)]:
            name = str(outcome.get("name") or outcome.get("outcome") or outcome.get("selection") or outcome.get("label") or outcome.get("option") or outcome.get("option_name") or outcome.get("team") or "").strip()
            price = outcome.get("price") or outcome.get("decimal_odds") or outcome.get("odds") or outcome.get("value") or outcome.get("odd") or outcome.get("decimal")
            point = self._float(outcome.get("point") or outcome.get("line") or outcome.get("points") or outcome.get("total") or outcome.get("handicap") or outcome.get("option_value"))
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
                if low.startswith("o") or low in {"over", "o"}:
                    add(bookmaker, "totals", "Over", price, point, market_name=raw_name)
                elif low.startswith("u") or low in {"under", "u"}:
                    add(bookmaker, "totals", "Under", price, point, market_name=raw_name)
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
        text = str(raw_name or "").lower().replace("-", "_").replace(" ", "_")
        if any(token in text for token in ("total", "over_under", "over/under", "goals_over", "goals_over_under", "goal_line")):
            return "totals"
        if any(token in text for token in ("spread", "handicap", "asian_handicap")):
            return "spreads"
        if ("both" in text and "score" in text) or "btts" in text:
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
            "rows_before_parse": 0,
            "odds_payload_rows": 0,
            "offers_parsed": 0,
            "empty_odds_payloads": 0,
            "parse_reject_reasons": {},
            "market_keys_seen": [],
            "option_keys_seen": [],
            "price_keys_seen": [],
            "line_keys_seen": [],
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "http_statuses": [],
            "top_level_keys": [],
            "last_body_preview": None,
            "active_odds_rows_seen": 0,
            "active_odds_targeted_matches": 0,
            "active_odds_targeted_offers_parsed": 0,
        }

    def _sportlogic_rate_limit_state_paths(self) -> list[Path]:
        return [
            Path(".data/line_history/sportlogic_rate_limit_open.json"),
            Path(".data/cache/sportlogic_rate_limit_open.json"),
        ]

    def _open_rate_limit_state(self) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        for path in self._sportlogic_rate_limit_state_paths():
            try:
                if not path.exists() or path.stat().st_size <= 0:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "").lower() != "open":
                continue
            expires_raw = payload.get("expires_at_utc")
            try:
                expires = parse_datetime(str(expires_raw)) if expires_raw else None
            except Exception:
                expires = None
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires is not None and expires.astimezone(UTC) <= now:
                continue
            return payload
        return None

    def _write_rate_limit_state(self, response: httpx.Response) -> None:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        try:
            cooldown = int(float(str(retry_after))) if retry_after not in (None, "") else int(float(os.getenv("SPORTLOGIC_429_COOLDOWN_SECONDS") or 90))
        except Exception:
            cooldown = 90
        cooldown = max(30, min(cooldown, int(float(os.getenv("SPORTLOGIC_429_MAX_COOLDOWN_SECONDS") or 3600))))
        now = datetime.now(UTC)
        payload = {
            "status": "open",
            "reason": "sportlogic_429_cooldown",
            "date_utc": self._today_utc_key(),
            "opened_at_utc": now.isoformat(),
            "expires_at_utc": (now + timedelta(seconds=cooldown)).isoformat(),
            "retry_after_seconds": cooldown,
            "status_code": getattr(response, "status_code", None),
            "url": str(getattr(response, "url", "")),
            "body_preview": str(getattr(response, "text", "") or "")[:1200],
        }
        for path in self._sportlogic_rate_limit_state_paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except Exception:
                continue

    def _sportlogic_daily_limit_state_paths(self) -> list[Path]:
        return [
            Path(".data/line_history/sportlogic_daily_limit_open.json"),
            Path(".data/cache/sportlogic_daily_limit_open.json"),
        ]

    def _today_utc_key(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _open_daily_limit_state(self) -> dict[str, Any] | None:
        today = self._today_utc_key()
        for path in self._sportlogic_daily_limit_state_paths():
            try:
                if not path.exists() or path.stat().st_size <= 0:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "").lower() != "open":
                continue
            if str(payload.get("date_utc") or "") != today:
                continue
            return payload
        return None

    def _write_daily_limit_state(self, response: httpx.Response) -> None:
        text = str(response.text or "")
        today = self._today_utc_key()
        payload = {
            "status": "open",
            "reason": "sportlogic_daily_429",
            "date_utc": today,
            "opened_at_utc": datetime.now(UTC).isoformat(),
            "status_code": response.status_code,
            "url": str(response.url),
            "body_preview": text[:1200],
        }
        try:
            body = response.json()
            if isinstance(body, dict):
                err = body.get("error") if isinstance(body.get("error"), dict) else {}
                nested = err.get("errors") if isinstance(err.get("errors"), dict) else {}
                payload["error_code"] = err.get("code") or body.get("code")
                payload["retry_after_seconds"] = nested.get("retry_after_seconds") or err.get("retry_after")
                payload["current"] = nested.get("current")
                payload["limit"] = nested.get("limit")
        except Exception:
            pass
        for path in self._sportlogic_daily_limit_state_paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except Exception:
                continue

    def _ready(self, stats: dict[str, Any]) -> bool:
        if not self.enabled:
            stats["enabled"] = False
            stats["reason"] = "disabled_by_config"
            return False
        if not self.api_key:
            stats["enabled"] = False
            stats["reason"] = "missing_api_key"
            return False
        rate_state = self._open_rate_limit_state()
        if rate_state is not None and self._env_bool("SPORTLOGIC_SKIP_WHEN_RATE_LIMIT_OPEN", True):
            stats["enabled"] = False
            stats["reason"] = "sportlogic_rate_limit_open"
            stats["rate_limited"] = True
            stats["rate_limit_state"] = rate_state
            stats["diagnosis"] = "sportlogic_rate_limit_open"
            return False
        state = self._open_daily_limit_state()
        if state is not None and self._env_bool("SPORTLOGIC_SKIP_WHEN_DAILY_LIMIT_OPEN", True):
            stats["enabled"] = False
            stats["reason"] = "sportlogic_daily_limit_open"
            stats["daily_limit_exceeded"] = True
            stats["daily_limit_state"] = state
            stats["diagnosis"] = "sportlogic_daily_limit_open"
            return False
        return True

    def _budget_left(self) -> bool:
        if getattr(self, "_rate_limited_this_run", False):
            return False
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
    def _game_id(row: dict[str, Any]) -> str:
        """Return the SportLogic game id, not the odds-row id.

        Odds rows have their own `id`, while `/games/{id}` and
        `/games/{id}/odds` require `game_id`. Prefer explicit game/fixture
        identifiers and only fall back to `id` for fixture-shaped rows.
        """
        for key in ("game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        for path in (("game", "id"), ("fixture", "id"), ("event", "id"), ("match", "id")):
            nested = SportLogicProvider._dig(row, *path)
            if nested not in (None, ""):
                return str(nested)
        if not SportLogicProvider._looks_like_odds_row(row):
            value = row.get("id")
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _event_id(row: dict[str, Any]) -> str:
        return SportLogicProvider._game_id(row)

    @staticmethod
    def _looks_like_odds_row(row: dict[str, Any]) -> bool:
        keys = {str(key).lower() for key in row.keys()}
        return bool(keys & {
            "market", "market_id", "market_name", "market_key", "option_name",
            "option_value", "outcome", "selection", "bookmaker", "bookmaker_id",
            "odds", "decimal_odds", "price", "is_suspended",
        })

    @staticmethod
    def _is_suspended(row: dict[str, Any]) -> bool:
        for key in ("is_suspended", "suspended", "isSuspended", "disabled", "is_disabled"):
            if key not in row:
                continue
            raw = str(row.get(key) or "").strip().lower()
            if raw in {"1", "true", "yes", "on", "suspended", "disabled"}:
                return True
        return False

    @staticmethod
    def _bookmaker_name(row: dict[str, Any]) -> str:
        for key in ("bookmaker", "bookmaker_name", "sportsbook", "provider", "book"):
            value = row.get(key)
            if isinstance(value, dict):
                nested = value.get("name") or value.get("title") or value.get("label") or value.get("key")
                if nested:
                    return str(nested).strip()
            elif value not in (None, ""):
                return str(value).strip()
        return "SportLogic"

    @staticmethod
    def _market_name_from_row(row: dict[str, Any]) -> str:
        market = row.get("market")
        if isinstance(market, dict):
            name = market.get("key") or market.get("name") or market.get("title") or market.get("label")
            if name:
                return str(name).strip()
            if market.get("id") not in (None, ""):
                return f"market_id_{market.get('id')}"
        for key in ("market_name", "market_key", "type", "market_type"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value).strip()
        market_id = str(row.get("market_id") or row.get("marketId") or "").strip()
        if market_id == "5":
            return "goals_over_under"
        if market_id:
            return f"market_id_{market_id}"
        return ""

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
