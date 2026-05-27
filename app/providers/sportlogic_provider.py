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
        self.per_page = max(1, min(100, int(float(os.getenv("SPORTLOGIC_PER_PAGE") or 100))))
        self.max_requests_per_run = max(
            0,
            int(float(
                getattr(settings, "sportlogic_per_run_max", None)
                or os.getenv("SPORTLOGIC_PER_RUN_MAX")
                or 80
            )),
        )
        self.odds_discovery_max_pages = max(
            1,
            int(float(
                os.getenv("SPORTLOGIC_ODDS_DISCOVERY_MAX_PAGES")
                or getattr(settings, "sportlogic_odds_discovery_max_pages", None)
                or 4
            )),
        )
        self.odds_discovery_game_detail_limit = max(
            0,
            int(float(
                os.getenv("SPORTLOGIC_ODDS_DISCOVERY_GAME_DETAIL_LIMIT")
                or getattr(settings, "sportlogic_odds_discovery_game_detail_limit", None)
                or 16
            )),
        )
        self.odds_discovery_min_detail_budget = max(
            0,
            int(float(os.getenv("SPORTLOGIC_ODDS_DISCOVERY_MIN_DETAIL_BUDGET") or 8)),
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
                fixtures.extend(await self._get_paginated_list(
                    client,
                    "/games",
                    {"date_from": date_key, "date_to": date_key, "per_page": self.per_page},
                    stats,
                    preview,
                    limit=self.match_limit * 3,
                ))

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
                cached_rows = []
                row_payload = item.get("row") if isinstance(item, dict) else None
                if isinstance(row_payload, dict):
                    raw_cached = row_payload.get("__sportlogic_odds_rows")
                    if isinstance(raw_cached, list):
                        cached_rows = [row for row in raw_cached if isinstance(row, dict)]
                if cached_rows:
                    rows = cached_rows
                    payload = {"source": "active_odds_discovery", "rows": cached_rows[:10]}
                    stats["odds_discovery_cached_rows_used"] = int(stats.get("odds_discovery_cached_rows_used", 0) or 0) + len(rows)
                else:
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

        stats["events_matched"] = len(mapping)
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
                fixtures.extend(await self._get_paginated_list(
                    client,
                    "/games",
                    {"date_from": date_key, "date_to": date_key, "per_page": self.per_page},
                    stats,
                    preview,
                    limit=max(self.match_limit * 3, len(matches) * 2),
                ))
        if not fixtures and self._env_bool("SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED", True):
            discovery_rows = await self._load_fixtures_from_active_odds(soccer_matches := matches, stats, preview)
            if discovery_rows:
                fixtures.extend(discovery_rows)

        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        stats["games_fetched"] = max(int(stats.get("games_fetched", 0) or 0), len(fixtures))
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview

    async def _load_fixtures_from_active_odds(
        self,
        matches: list[Match],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Discover SportLogic games from active odds without draining the run budget.

        Some free SportLogic accounts return zero rows from /games for current
        date filters, while /odds?is_active=true is populated.  The old runtime
        guard scanned /odds until the run budget was exhausted, then had no
        requests left to fetch game details.  This core fallback scans only a
        small number of odds pages, preserves a detail budget, and reuses odds
        rows that already contain embedded fixture/game payloads.
        """
        if not matches or not self._budget_left():
            return []
        max_pages = max(1, int(getattr(self, "odds_discovery_max_pages", 4) or 4))
        detail_limit = max(0, int(getattr(self, "odds_discovery_game_detail_limit", 16) or 16))
        reserve = max(0, int(getattr(self, "odds_discovery_min_detail_budget", 8) or 8))
        per_page = max(10, min(100, int(getattr(self, "per_page", 100) or 100)))
        requested_dates = {m.commence_time.astimezone(UTC).date().isoformat() for m in matches if getattr(m, "commence_time", None)}
        direct_fixtures: list[dict[str, Any]] = []
        odds_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen_game_ids: list[str] = []
        cursor: str | None = None
        scanned_pages = 0
        scanned_rows = 0
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            while self._budget_left() and scanned_pages < max_pages:
                if self.max_requests_per_run > 0 and (self.max_requests_per_run - self._requests) <= reserve:
                    stats["odds_discovery_stopped_for_detail_budget"] = True
                    break
                params: dict[str, Any] = {"is_active": "true", "per_page": per_page}
                if cursor:
                    params["cursor"] = cursor
                payload = await self._get_json(client, "/odds", params, stats, preview)
                scanned_pages += 1
                rows = self._extract_odds_rows(payload)
                scanned_rows += len(rows)
                if rows and len(preview.get("sample_odds", [])) < 3:
                    preview.setdefault("sample_odds", []).extend(rows[: max(0, 3 - len(preview.get("sample_odds", [])))])
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    game_id = self._event_id(row) or self._first_nested_id(row, "game", "fixture", "event", "match")
                    if game_id:
                        odds_by_game[str(game_id)].append(row)
                        if str(game_id) not in seen_game_ids:
                            seen_game_ids.append(str(game_id))
                    fixture = self._fixture_from_odds_row(row)
                    if fixture:
                        if game_id:
                            fixture.setdefault("id", game_id)
                        fixture.setdefault("__sportlogic_odds_rows", []).append(row)
                        if self._fixture_is_relevant(fixture, matches, requested_dates):
                            direct_fixtures.append(fixture)
                cursor = self._next_cursor(payload)
                if not cursor or not rows:
                    break
                if len(seen_game_ids) >= detail_limit and direct_fixtures:
                    break
            detail_rows: list[dict[str, Any]] = []
            detail_budget = min(detail_limit, len(seen_game_ids))
            for game_id in seen_game_ids[:detail_budget]:
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                payload = await self._get_json(client, f"/games/{game_id}", {}, stats, preview)
                row = self._detail_row(payload)
                if not row:
                    continue
                row.setdefault("id", game_id)
                cached = odds_by_game.get(str(game_id), [])
                if cached:
                    row["__sportlogic_odds_rows"] = cached
                if self._fixture_is_relevant(row, matches, requested_dates):
                    detail_rows.append(row)
        fixtures = self._dedupe_fixture_rows(direct_fixtures + detail_rows)
        stats["odds_discovery_requests_used"] = scanned_pages
        stats["odds_discovery_rows"] = scanned_rows
        stats["odds_discovery_game_ids"] = len(seen_game_ids)
        stats["odds_discovery_direct_fixtures"] = len(direct_fixtures)
        stats["odds_discovery_detail_fixtures"] = len(detail_rows)
        stats["odds_discovery_fixtures"] = len(fixtures)
        stats["odds_discovery_max_pages_effective"] = max_pages
        stats["odds_discovery_detail_limit_effective"] = detail_limit
        if fixtures:
            stats["diagnosis"] = "active_odds_discovery_built_fixtures"
        elif scanned_rows:
            stats["diagnosis"] = "active_odds_found_but_no_matchable_current_fixture"
        return fixtures

    def _fixture_from_odds_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("game", "fixture", "event", "match"):
            value = row.get(key)
            if isinstance(value, dict):
                candidate = dict(value)
                if self._team_name(candidate, "home") and self._team_name(candidate, "away"):
                    return candidate
        if self._team_name(row, "home") and self._team_name(row, "away"):
            return dict(row)
        return None

    def _fixture_is_relevant(self, row: dict[str, Any], matches: list[Match], requested_dates: set[str]) -> bool:
        dt = self._fixture_datetime(row)
        if dt is not None:
            row_date = dt.astimezone(UTC).date().isoformat()
            if row_date in requested_dates:
                return True
        home = self._team_name(row, "home")
        away = self._team_name(row, "away")
        if not home or not away:
            return False
        for match in matches:
            try:
                # If a provider detail lacks a date, accept only a strong name
                # match.  The later _match_fixtures call still enforces time
                # when SportLogic supplies it.
                mh = str(match.home_team or "").lower()
                ma = str(match.away_team or "").lower()
                rh = home.lower()
                ra = away.lower()
                if (rh == mh and ra == ma) or (rh == ma and ra == mh):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _detail_row(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            for key in ("data", "game", "fixture", "event", "match", "result"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    return item
        return None

    @staticmethod
    def _first_nested_id(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if isinstance(value, dict):
                nested = value.get("id") or value.get("game_id") or value.get("event_id") or value.get("fixture_id")
                if nested not in (None, ""):
                    return str(nested)
        return ""

    def _dedupe_fixture_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            event_id = self._event_id(row)
            home = self._team_name(row, "home")
            away = self._team_name(row, "away")
            dt = self._fixture_datetime(row)
            key = event_id or f"{home}|{away}|{dt.isoformat() if dt else ''}"
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

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

    async def _get_paginated_list(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None,
        stats: dict[str, Any],
        preview: dict[str, Any],
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        **_compat: Any,
    ) -> list[dict[str, Any]]:
        """Fetch SportLogic cursor/page envelopes defensively.

        Kept as a first-class method because older runtime wrappers and smoke
        scripts call this name directly.  It also fixes free-plan coverage by
        following cursor/next_page values instead of treating the first page as
        the complete fixture set.
        """
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        page = 1
        per_page = int((params or {}).get("per_page") or self.per_page)
        seen_cursors: set[str] = set()
        max_pages = max(1, int(max_pages or getattr(self, "sportlogic_max_pages", 0) or os.getenv("SPORTLOGIC_MAX_GAME_PAGES_PER_RUN") or 5))
        while self._budget_left():
            query = dict(params or {})
            query.setdefault("per_page", per_page)
            if cursor:
                query["cursor"] = cursor
            elif page > 1:
                query.setdefault("page", page)
            payload = await self._get_json(client, path, query, stats, preview)
            batch = self._extract_list(payload)
            rows.extend(batch)
            if limit is not None and len(rows) >= limit:
                return rows[:limit]
            next_cursor = self._next_cursor(payload)
            if next_cursor:
                if next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                continue
            if not batch or len(batch) < per_page:
                break
            page += 1
            # Avoid accidental quota drain if a provider ignores pagination.
            if page > max_pages:
                break
        return rows

    @staticmethod
    def _next_cursor(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        direct_keys = ("next_cursor", "nextCursor", "cursor_next", "cursor", "next")
        for key in direct_keys:
            value = payload.get(key)
            if value not in (None, "", False):
                text = str(value)
                if text.lower() not in {"none", "null", "false"}:
                    return text
        for key in ("meta", "pagination", "paging", "links"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                found = SportLogicProvider._next_cursor(nested)
                if found:
                    return found
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
            ("/odds", {"fixture_id": event_id}),
            ("/odds", {"event_id": event_id}),
            ("/games/odds", {"game_id": event_id}),
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
        for key in ("data", "response", "results", "fixtures", "matches", "events", "items", "games", "odds"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = SportLogicProvider._extract_list(value)
                if nested:
                    return nested
        # Some envelopes use a single result object.
        for key in ("game", "fixture", "event", "match", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                return [value]
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
            if self._is_suspended_odds_row(row):
                reject("suspended_odds")
                continue
            # Shape A: bookmakers -> markets -> outcomes
            bookmakers = row.get("bookmakers") if isinstance(row, dict) else None
            if isinstance(bookmakers, list):
                for bookmaker_payload in [x for x in bookmakers if isinstance(x, dict)]:
                    book = self._bookmaker_name_from_payload(bookmaker_payload)
                    for market in self._market_rows(bookmaker_payload):
                        self._parse_market(market, match, book, add)
                continue

            # Shape B: markets at top level
            for market in self._market_rows(row):
                book = self._bookmaker_name_from_payload(row)
                self._parse_market(market, match, book, add)

            # Shape C: flattened odds fields
            book = self._bookmaker_name_from_payload(row)
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
        price_fields = {"price", "decimal_odds", "value", "odd", "odds", "decimal", "option_value"}
        point_fields = {"total", "handicap", "line", "points", "point"}
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
        market_name = str(row.get("market") or row.get("market_name") or row.get("market_key") or row.get("market_id") or row.get("bet_type") or row.get("type") or "").strip()
        selection_name = str(row.get("outcome") or row.get("selection") or row.get("label") or row.get("option") or row.get("option_name") or row.get("name") or "").strip()
        price = row.get("price") or row.get("decimal_odds") or row.get("decimalPrice") or row.get("value") or row.get("odd") or row.get("odds") or row.get("decimal") or row.get("option_value")
        point = self._float(row.get("total") or row.get("handicap") or row.get("line") or row.get("points") or row.get("point"))
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
            if "under" in combined or low.startswith("u"):
                add(book, "totals", "Under", price, point, market_name=market_name)
            elif "over" in combined or low.startswith("o"):
                add(book, "totals", "Over", price, point, market_name=market_name)
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
    def _is_suspended_odds_row(row: dict[str, Any]) -> bool:
        for key in ("is_suspended", "suspended", "isSuspended", "inactive", "is_active"):
            if key not in row:
                continue
            value = row.get(key)
            text = str(value).strip().lower()
            if key == "is_active":
                return text in {"0", "false", "no"}
            if isinstance(value, bool):
                return value
            if text in {"1", "true", "yes", "suspended", "inactive"}:
                return True
        return False

    @staticmethod
    def _bookmaker_name_from_payload(row: dict[str, Any]) -> str:
        for key in ("bookmaker", "book", "sportsbook", "provider"):
            value = row.get(key)
            if isinstance(value, dict):
                for nested_key in ("name", "title", "slug", "key"):
                    nested = value.get(nested_key)
                    if nested not in (None, ""):
                        return str(nested)
            if value not in (None, ""):
                return str(value)
        for key in ("bookmaker_name", "bookmaker_slug", "sportsbook_name", "provider_name", "title", "name"):
            value = row.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        return "SportLogic"

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
            price = outcome.get("price") or outcome.get("decimal_odds") or outcome.get("decimalPrice") or outcome.get("odds") or outcome.get("value") or outcome.get("odd") or outcome.get("decimal") or outcome.get("option_value")
            point = self._float(outcome.get("point") or outcome.get("line") or outcome.get("points") or outcome.get("total") or outcome.get("handicap"))
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
        text = str(raw_name or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not text:
            return "h2h"
        if any(token in text for token in ("btts", "both_teams_to_score", "bothteams", "обе_забьют")):
            return "btts"
        if (
            any(token in text for token in ("total", "over_under", "over/under", "goals_over", "goals_under", "больше_меньше"))
            or re.search(r"(?:^|_)(?:over|under|o|u)_?\d+(?:[._]\d+)?", text)
            or re.search(r"(?:^|_)(?:over|under)(?:$|_)", text)
        ):
            return "totals"
        if any(token in text for token in ("spread", "handicap", "asian_handicap", "ah", "фора")):
            return "spreads"
        if any(token in text for token in ("double_chance", "doublechance")):
            return "doubleChance"
        if any(token in text for token in ("draw_no_bet", "dnb")):
            return "dnb"
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
        direct_keys = [f"{side}_team", f"{side}Team", side, f"{side}_name", f"{side}Name", f"{side}_participant", f"{side}Participant"]
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
            row.get("starts_at"),
            row.get("start_at"),
            row.get("scheduled_at"),
            row.get("kickoff"),
            row.get("kickoff_at"),
            row.get("date_time"),
            row.get("datetime"),
            row.get("timestamp"),
            row.get("startsAt"),
            row.get("startTime"),
            SportLogicProvider._dig(row, "fixture", "date"),
            SportLogicProvider._dig(row, "game", "start_time"),
            SportLogicProvider._dig(row, "game", "starts_at"),
            SportLogicProvider._dig(row, "event", "start_time"),
            SportLogicProvider._dig(row, "match", "start_time"),
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
        for key in ("id", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId", "game_id", "gameId"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        for path in (("fixture", "id"), ("game", "id"), ("event", "id"), ("match", "id")):
            nested = SportLogicProvider._dig(row, *path)
            if nested not in (None, ""):
                return str(nested)
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
