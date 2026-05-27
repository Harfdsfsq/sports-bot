from __future__ import annotations

import os
import re
import json
import asyncio
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
                getattr(settings, "sportlogic_per_run_max", None)
                or os.getenv("SPORTLOGIC_PER_RUN_MAX")
                or 80
            )),
        )
        self.max_pages = max(
            1,
            int(float(
                getattr(settings, "sportlogic_max_pages", None)
                or os.getenv("SPORTLOGIC_MAX_PAGES")
                or os.getenv("SPORTLOGIC_GAMES_MAX_PAGES")
                or 5
            )),
        )
        self.enabled = self._env_bool("ENABLE_SPORTLOGIC", True) and self._env_bool("SPORTLOGIC_ENABLED", True)
        self.min_request_interval_seconds = max(
            0.0,
            float(
                getattr(settings, "sportlogic_min_request_interval_seconds", None)
                or os.getenv("SPORTLOGIC_MIN_REQUEST_INTERVAL_SECONDS")
                or 0.0
            ),
        )
        self._requests = 0
        self._last_request_ts = 0.0
        self._rate_limit_cooldown_path = Path(os.getenv("SPORTLOGIC_RATE_LIMIT_COOLDOWN_FILE") or ".data/cache/sportlogic_rate_limit_cooldown.json")
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
        date_from = now.date().isoformat()
        # SportLogic date_to behaves like an upper day boundary on some accounts;
        # ask through the next day so today's UTC evening fixtures are not lost.
        date_to = (now + timedelta(days=days_ahead + 1)).date().isoformat()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            # SportLogic docs: GET /games uses date_from/date_to/status and cursor pagination.
            # One window request avoids wasting the free 10 RPM bucket on one request per day.
            # Fallback without status is kept for accounts returning localized/legacy status values.
            status_variants = ["scheduled", "notstarted", "not_started", "pending", "upcoming", ""]
            for status_value in status_variants:
                params = {"date_from": date_from, "date_to": date_to, "per_page": 100}
                if status_value:
                    params["status"] = status_value
                if not self._budget_left():
                    stats["budget_exhausted"] = True
                    break
                rows = await self._get_paginated_list(
                    client,
                    "/games",
                    params,
                    stats,
                    preview,
                    max_pages=self.max_pages,
                )
                fixtures.extend(rows)
                if rows:
                    break
            if not fixtures and self._env_bool("SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED", True) and self._budget_left():
                discovered = await self._discover_fixtures_from_active_odds(client, stats, preview)
                fixtures.extend(discovered)

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
            if dates:
                date_from = dates[0]
                # Use the day after the last target date to avoid empty responses
                # from accounts where date_to is treated as an exclusive boundary.
                date_to = (datetime.fromisoformat(dates[-1]).date() + timedelta(days=1)).isoformat()
                status_variants = ["scheduled", "notstarted", "not_started", "pending", "upcoming", ""]
                for status_value in status_variants:
                    params = {"date_from": date_from, "date_to": date_to, "per_page": 100}
                    if status_value:
                        params["status"] = status_value
                    if not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    rows = await self._get_paginated_list(
                        client,
                        "/games",
                        params,
                        stats,
                        preview,
                        max_pages=self.max_pages,
                    )
                    fixtures.extend(rows)
                    if rows:
                        break
                if not fixtures and self._env_bool("SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED", True) and self._budget_left():
                    discovered = await self._discover_fixtures_from_active_odds(client, stats, preview)
                    fixtures.extend(discovered)
        self._fixture_cache = fixtures
        stats["fixtures_fetched"] = len(fixtures)
        preview["sample_fixtures"] = fixtures[:3]
        return fixtures, stats, preview


    def _fixture_from_odds_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build a fixture-like row from an /odds discovery row when possible.

        Some SportLogic /odds responses embed a `game` object while /games/{id}
        details may return stale rows outside the requested date window.  When
        the odds row already contains the game payload, use it directly so the
        provider can become useful without spending another request.
        """
        if not isinstance(row, dict):
            return None
        for key in ("game", "fixture", "match", "event"):
            value = row.get(key)
            if isinstance(value, dict):
                candidate = dict(value)
                candidate.setdefault("id", row.get("game_id") or row.get("event_id") or row.get("fixture_id") or candidate.get("id"))
                if self._team_name(candidate, "home") and self._team_name(candidate, "away") and self._fixture_datetime(candidate):
                    return candidate
        # Some flattened rows contain enough game fields directly.
        if self._team_name(row, "home") and self._team_name(row, "away") and self._fixture_datetime(row):
            candidate = dict(row)
            candidate.setdefault("id", row.get("game_id") or row.get("event_id") or row.get("fixture_id"))
            return candidate
        return None

    async def _discover_fixtures_from_active_odds(
        self,
        client: httpx.AsyncClient,
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Fallback when documented /games date window returns no current rows.

        SportLogic's documented /odds endpoint is cursor-paginated and can be
        filtered by active odds. Rows include game_id, so we fetch a small number
        of /games/{id} details while staying inside the free minute/day bucket.
        """
        stats["odds_discovery_fallback_enabled"] = True
        max_pages = max(1, int(float(os.getenv("SPORTLOGIC_ODDS_DISCOVERY_MAX_PAGES") or 4)))
        max_games = max(1, int(float(os.getenv("SPORTLOGIC_ODDS_DISCOVERY_GAME_DETAIL_LIMIT") or 60)))
        odds_rows: list[dict[str, Any]] = []
        seen_odds: set[str] = set()
        # Try several documented boolean encodings.  Do not stop on the first
        # non-empty batch if it only contains stale/non-matchable rows.
        for params in (
            {"is_active": "true", "per_page": 100},
            {"is_active": "1", "per_page": 100},
            {"per_page": 100},
        ):
            if not self._budget_left():
                stats["budget_exhausted"] = True
                break
            batch = await self._get_paginated_list(client, "/odds", params, stats, preview, max_pages=max_pages)
            preview.setdefault("odds_discovery_variants", []).append({"params": params, "rows": len(batch)})
            for row in batch:
                sig = str(row.get("id") or row.get("game_id") or row)
                if sig in seen_odds:
                    continue
                seen_odds.add(sig)
                odds_rows.append(row)
            if len(odds_rows) >= max_games * 4:
                break
        stats["odds_discovery_rows"] = len(odds_rows)
        game_ids: list[str] = []
        seen: set[str] = set()
        fixtures: list[dict[str, Any]] = []
        embedded_fixtures = 0
        preview.setdefault("sample_odds", [])
        for row in odds_rows[:10]:
            if isinstance(preview.get("sample_odds"), list) and len(preview["sample_odds"]) < 5:
                preview["sample_odds"].append(self._sanitize(row))
        for row in odds_rows:
            embedded = self._fixture_from_odds_row(row)
            if embedded is not None:
                fixtures.append(embedded)
                embedded_fixtures += 1
            gid = row.get("game_id") or row.get("event_id") or row.get("fixture_id")
            if gid in (None, ""):
                continue
            sid = str(gid)
            if sid in seen:
                continue
            seen.add(sid)
            game_ids.append(sid)
            if len(game_ids) >= max_games:
                break
        for gid in game_ids:
            if not self._budget_left():
                stats["budget_exhausted"] = True
                break
            payload = await self._get_json(client, f"/games/{gid}", {}, stats, preview)
            row = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
            if isinstance(row, dict) and (row.get("id") or row.get("game_id")):
                fixtures.append(row)
        stats["odds_discovery_game_ids"] = len(game_ids)
        stats["odds_discovery_embedded_fixtures"] = embedded_fixtures
        stats["odds_discovery_fixtures"] = len(fixtures)
        if fixtures:
            preview.setdefault("sample_fixtures", [])
            preview["sample_fixtures"] = (preview.get("sample_fixtures") or []) + fixtures[:3]
        return fixtures

    async def _get_paginated_list(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        stats: dict[str, Any],
        preview: dict[str, Any],
        *,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all cursor-paginated rows for SportLogic list endpoints.

        SportLogic list endpoints use an opaque cursor from pagination.next_cursor
        or meta.next_cursor.  A single per_page=100 call silently truncates days
        with more than 100 rows, which breaks the 300-match inventory target.
        """
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = max(1, int(max_pages or self.max_pages or 1))
        for page_no in range(pages):
            if not self._budget_left():
                stats["budget_exhausted"] = True
                break
            page_params = dict(params or {})
            if cursor:
                page_params["cursor"] = cursor
            payload = await self._get_json(client, path, page_params, stats, preview)
            page_rows = self._extract_list(payload)
            rows.extend(page_rows)
            stats["pages_fetched"] = int(stats.get("pages_fetched") or 0) + 1
            next_cursor = self._next_cursor(payload)
            if not next_cursor:
                break
            cursor = next_cursor
            if not page_rows and page_no > 0:
                break
        return rows

    @staticmethod
    def _next_cursor(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        containers = [payload]
        for key in ("pagination", "meta", "links"):
            value = payload.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            for key in ("next_cursor", "cursor", "nextCursor"):
                value = container.get(key)
                if value not in (None, "", False):
                    return str(value)
            if container.get("has_more") is False:
                return None
        next_value = payload.get("next")
        if isinstance(next_value, str) and next_value.strip():
            # Some envelopes expose a full next URL.  Extract cursor=... when present.
            match = re.search(r"[?&]cursor=([^&]+)", next_value)
            return match.group(1) if match else next_value
        return None

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> Any | None:
        await self._respect_rate_limit()
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
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            if retry_after:
                stats["retry_after"] = retry_after
            self._record_rate_limit_cooldown(response, stats)
            # Stop spending the whole SportLogic per-run budget after the first
            # quota response.  The provider has a small free minute/day bucket,
            # so repeated variants only create errors and block useful work.
            self._requests = max(self._requests, self.max_requests_per_run)
            stats["budget_exhausted"] = True
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
            # Shape A: bookmakers -> markets -> outcomes
            bookmakers = row.get("bookmakers") if isinstance(row, dict) else None
            if isinstance(bookmakers, list):
                for bookmaker_payload in [x for x in bookmakers if isinstance(x, dict)]:
                    book = str(bookmaker_payload.get("name") or bookmaker_payload.get("bookmaker") or bookmaker_payload.get("sportsbook") or bookmaker_payload.get("provider") or bookmaker_payload.get("title") or "SportLogic")
                    for market in self._market_rows(bookmaker_payload):
                        self._parse_market(market, match, book, add)
                continue

            # Shape B: markets at top level
            for market in self._market_rows(row):
                book = self._bookmaker_label(row)
                self._parse_market(market, match, book, add)

            # Shape C: flattened odds fields
            book = self._bookmaker_label(row)
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
        market_name = self._market_label(row)
        selection_name = self._selection_label(row)
        price = self._price_value(row)
        point = self._line_value(row)
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
        raw_name = self._market_label(market).lower()
        outcomes = market.get("outcomes") or market.get("values") or market.get("selections") or market.get("options") or market.get("odds")
        if isinstance(outcomes, dict):
            outcomes = [{"name": key, "price": value} for key, value in outcomes.items()]
        if not isinstance(outcomes, list):
            outcomes = []

        family = self._market_family(raw_name)
        for outcome in [x for x in outcomes if isinstance(x, dict)]:
            name = self._selection_label(outcome)
            price = self._price_value(outcome)
            point = self._line_value(outcome)
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
                if point is None:
                    point = self._float(outcome.get("option_value"))
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
        text = str(raw_name or "").lower()
        flat = re.sub(r"[^a-z0-9]+", "", text)
        if any(token in flat for token in ("goalsoverunder", "overunder", "total", "totals")) or any(token in text for token in ("goals over", "over/under")):
            return "totals"
        if any(token in flat for token in ("spread", "handicap", "asianhandicap")):
            return "spreads"
        if ("both" in text and "score" in text) or "btts" in flat:
            return "btts"
        return "h2h"

    @staticmethod
    def _dict_label(value: Any, *keys: str) -> str:
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item not in (None, "") and not isinstance(item, (dict, list)):
                    return str(item).strip()
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value).strip()
        return ""

    def _market_label(self, row: dict[str, Any]) -> str:
        value = row.get("market") or row.get("market_name") or row.get("market_key") or row.get("type") or row.get("name")
        if isinstance(value, dict):
            return self._dict_label(value, "key", "name", "label", "category")
        return self._dict_label(value)

    def _selection_label(self, row: dict[str, Any]) -> str:
        value = row.get("outcome") or row.get("selection") or row.get("label") or row.get("option") or row.get("option_name") or row.get("name") or row.get("team")
        return self._dict_label(value, "name", "label", "key", "title")

    def _price_value(self, row: dict[str, Any]) -> Any:
        # SportLogic docs return decimal odds in the `odds` field; option_value is the line (e.g. 2.5), not a price.
        for key in ("price", "decimal_odds", "odds", "value", "odd", "decimal"):
            value = row.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return value
        return None

    def _line_value(self, row: dict[str, Any]) -> float | None:
        for key in ("point", "line", "points", "total", "handicap", "option_value"):
            value = row.get(key)
            parsed = self._float(value)
            if parsed is not None:
                return parsed
        return None

    def _bookmaker_label(self, row: dict[str, Any]) -> str:
        value = row.get("bookmaker") or row.get("bookmaker_name") or row.get("sportsbook") or row.get("provider") or row.get("book")
        if isinstance(value, dict):
            return self._dict_label(value, "name", "slug", "title", "key") or "SportLogic"
        return self._dict_label(value) or "SportLogic"

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
            "pages_fetched": 0,
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
        cooldown = self._active_rate_limit_cooldown()
        if cooldown:
            stats["enabled"] = True
            stats["rate_limited"] = True
            stats["budget_exhausted"] = True
            stats["reason"] = "sportlogic_rate_limit_cooldown"
            stats["cooldown_until_utc"] = cooldown.get("until_utc")
            return False
        return True

    def _budget_left(self) -> bool:
        if self._active_rate_limit_cooldown():
            return False
        return self.max_requests_per_run <= 0 or self._requests < self.max_requests_per_run

    def _active_rate_limit_cooldown(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._rate_limit_cooldown_path.read_text(encoding="utf-8"))
            until_raw = str(payload.get("until_utc") or "")
            if not until_raw:
                return None
            until = parse_datetime(until_raw)
            if until > datetime.now(UTC):
                return payload
        except Exception:
            return None
        return None

    def _record_rate_limit_cooldown(self, response: httpx.Response, stats: dict[str, Any]) -> None:
        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
        wait_seconds: float | None = None
        if retry_after:
            try:
                wait_seconds = float(str(retry_after).strip())
            except Exception:
                wait_seconds = None
        if wait_seconds is None:
            # SportLogic has both 10 RPM and 500/day free limits.  Without a
            # Retry-After header we assume minute-bucket pressure and cool down
            # long enough to avoid burning the next run with immediate 429s.
            wait_seconds = float(os.getenv("SPORTLOGIC_429_COOLDOWN_SECONDS") or 90)
        until = datetime.now(UTC) + timedelta(seconds=max(30.0, wait_seconds))
        stats["retry_after_seconds"] = round(max(30.0, wait_seconds), 2)
        stats["cooldown_until_utc"] = until.isoformat()
        try:
            self._rate_limit_cooldown_path.parent.mkdir(parents=True, exist_ok=True)
            self._rate_limit_cooldown_path.write_text(
                json.dumps(
                    {
                        "provider": "sportlogic",
                        "reason": "http_429",
                        "status_code": response.status_code,
                        "until_utc": until.isoformat(),
                        "recorded_at_utc": datetime.now(UTC).isoformat(),
                        "x_ratelimit_limit": response.headers.get("X-RateLimit-Limit"),
                        "x_ratelimit_remaining": response.headers.get("X-RateLimit-Remaining"),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    async def _respect_rate_limit(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        import time
        now = time.monotonic()
        if self._last_request_ts <= 0:
            self._last_request_ts = now
            return
        wait = self.min_request_interval_seconds - (now - self._last_request_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_ts = time.monotonic()

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
            SportLogicProvider._dig(row, "game", "start_time"),
            SportLogicProvider._dig(row, "game", "commence_time"),
            SportLogicProvider._dig(row, "match", "start_time"),
            SportLogicProvider._dig(row, "event", "start_time"),
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
        for nested_path in (("fixture", "id"), ("game", "id"), ("event", "id"), ("match", "id")):
            nested = SportLogicProvider._dig(row, *nested_path)
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
