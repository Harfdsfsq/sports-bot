from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import is_simulated_or_esports_event, normalize_bookmaker_name, parse_datetime, score_event_match


class OddsPapiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = getattr(settings, "oddspapi_api_key", None)
        self.base_url = str(getattr(settings, "oddspapi_base_url", "https://api.oddspapi.io/v4")).rstrip("/")
        self.timeout = float(getattr(settings, "oddspapi_timeout_seconds", 12.0) or 12.0)
        self.min_interval = max(30, int(getattr(settings, "oddspapi_min_fetch_interval_minutes", 360) or 360))
        self.match_limit = max(1, int(getattr(settings, "oddspapi_match_limit", 16) or 16))
        self.tournament_limit = max(1, int(getattr(settings, "oddspapi_tournament_limit", 4) or 4))
        self.max_tournament_ids_per_request = 5

    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "rate_limit_retries": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "matches_built": 0,
            "low_tier_skipped": 0,
            "simulated_skipped": 0,
            "http_statuses": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": []}
        if not self.api_key:
            return [], stats, preview

        now = datetime.now(UTC)
        from_ts = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        to_ts = (now + timedelta(days=max(1, int(getattr(self.settings, "run_days_ahead", 2) or 2)))).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            fixtures = await self._get_json(
                client,
                "/fixtures",
                stats,
                params={"sportId": 10, "from": from_ts, "to": to_ts, "statusId": 0, "hasOdds": "true", "apiKey": self.api_key},
            )
        rows = [row for row in fixtures if isinstance(row, dict)] if isinstance(fixtures, list) else []
        stats["fixtures_fetched"] = len(rows)
        preview["sample_fixtures"] = rows[:3]

        matches: list[Match] = []
        seen_ids: set[str] = set()
        for row in rows:
            match = self._build_match(row)
            if match is None:
                league_name = str(row.get("tournamentName") or "")
                home = str(row.get("participant1Name") or "")
                away = str(row.get("participant2Name") or "")
                if is_simulated_or_esports_event(home, away, league_name):
                    stats["simulated_skipped"] += 1
                continue
            if match.source_event_id in seen_ids:
                continue
            seen_ids.add(match.source_event_id)
            if match.tier == "low" and not bool(getattr(self.settings, "allow_low_tier", False)):
                stats["low_tier_skipped"] += 1
                continue
            matches.append(match)
        stats["matches_built"] = len(matches)
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
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "cache_hit": False,
            "requests": 0,
            "rate_limit_retries": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "events_matched": 0,
            "offers_parsed": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "tournament_ids_selected": 0,
            "tournament_batches": 0,
            "http_statuses": [],
            "last_body_preview": None,
            "families_supported": ["h2h"],
            "bookmakers_requested": [],
            "bootstrap_fixtures_reused": 0,
        }
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_odds": []}
        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        cached = self._load_cached_offers(soccer_matches)
        if cached is not None:
            stats["cache_hit"] = True
            return cached, stats, preview

        prioritized = self._prioritize_matches(soccer_matches)[: self.match_limit]
        now = datetime.now(UTC)
        from_ts = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        to_ts = (now + timedelta(days=max(1, int(getattr(self.settings, "run_days_ahead", 2) or 2)))).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        bookmakers = self._bookmaker_slugs()
        stats["bookmakers_requested"] = bookmakers
        if not bookmakers:
            return {}, stats, preview

        reused_rows: list[dict[str, Any]] = []
        seen_fixture_ids: set[str] = set()
        for match in prioritized:
            raw_fixture = match.metadata.get("oddspapi_raw_fixture") if isinstance(match.metadata, dict) else None
            fixture_id = str(raw_fixture.get("fixtureId") or "") if isinstance(raw_fixture, dict) else ""
            if match.source != "oddspapi" or not fixture_id or fixture_id in seen_fixture_ids:
                continue
            seen_fixture_ids.add(fixture_id)
            reused_rows.append(raw_fixture)
        if reused_rows:
            stats["fixtures_fetched"] = len(reused_rows)
            stats["bootstrap_fixtures_reused"] = len(reused_rows)
            preview["sample_fixtures"] = reused_rows[:3]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            rows = reused_rows
            if not rows:
                fixtures = await self._get_json(
                    client,
                    "/fixtures",
                    stats,
                    params={"sportId": 10, "from": from_ts, "to": to_ts, "statusId": 0, "hasOdds": "true", "apiKey": self.api_key},
                )
                rows = [row for row in fixtures if isinstance(row, dict)] if isinstance(fixtures, list) else []
                stats["fixtures_fetched"] = len(rows)
                preview["sample_fixtures"] = rows[:3]

            matched: dict[str, dict[str, Any]] = {}
            for row in rows:
                fixture = self._match_fixture(row, prioritized)
                if fixture is None:
                    continue
                match, quality = fixture
                if match.match_key not in matched:
                    matched[match.match_key] = {"match": match, "row": row, "quality": quality}
                    if quality == "exact":
                        stats["matched_exact"] += 1
                    elif quality == "loose":
                        stats["matched_loose"] += 1
                    elif quality == "fuzzy":
                        stats["matched_fuzzy"] += 1
            stats["events_matched"] = len(matched)
            tournament_ids: list[str] = []
            fixture_to_match: dict[str, Match] = {}
            for item in matched.values():
                row = item["row"]
                fixture_to_match[str(row.get("fixtureId") or "")] = item["match"]
                tid = str(row.get("tournamentId") or "")
                if tid and tid not in tournament_ids:
                    tournament_ids.append(tid)
            tournament_ids = tournament_ids[: self.tournament_limit]
            stats["tournament_ids_selected"] = len(tournament_ids)
            if not tournament_ids:
                output: dict[str, list[Offer]] = {}
                self._write_cache(output)
                return output, stats, preview

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            for bookmaker_slug in bookmakers:
                for tournament_batch in self._chunked(tournament_ids, self.max_tournament_ids_per_request):
                    stats["tournament_batches"] = int(stats.get("tournament_batches", 0) or 0) + 1
                    odds_rows = await self._get_json(
                        client,
                        "/odds-by-tournaments",
                        stats,
                        params={
                            "tournamentIds": ",".join(tournament_batch),
                            "bookmaker": bookmaker_slug,
                            "apiKey": self.api_key,
                        },
                    )
                    rows = [row for row in odds_rows if isinstance(row, dict)] if isinstance(odds_rows, list) else []
                    if rows and len(preview["sample_odds"]) < 3:
                        preview["sample_odds"].append(rows[0])
                    for row in rows:
                        fixture_id = str(row.get("fixtureId") or "")
                        match = fixture_to_match.get(fixture_id)
                        if match is None:
                            continue
                        offers = self._parse_fixture_odds(row, match, bookmaker_slug)
                        if not offers:
                            continue
                        offers_by_match[match.match_key].extend(offers)
                        stats["offers_parsed"] += len(offers)

        output = {k: v for k, v in offers_by_match.items() if v}
        self._write_cache(output)
        return output, stats, preview

    async def _get_json(self, client: httpx.AsyncClient, path: str, stats: dict[str, Any], params: dict[str, Any]) -> Any | None:
        for attempt in range(2):
            stats["requests"] += 1
            try:
                response = await client.get(f"{self.base_url}{path}", params=params)
            except Exception as exc:
                stats["response_errors"] += 1
                stats["last_body_preview"] = f"request failed: {exc}"
                return None
            stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:1800]
            if response.status_code == 429 and attempt == 0:
                stats["rate_limit_retries"] = int(stats.get("rate_limit_retries", 0) or 0) + 1
                await asyncio.sleep(self._retry_delay_seconds(response))
                continue
            if response.status_code != 200:
                stats["response_errors"] += 1
                return None
            try:
                return response.json()
            except Exception:
                stats["response_errors"] += 1
                return None
        stats["response_errors"] += 1
        return None

    @staticmethod
    def _retry_delay_seconds(response: httpx.Response) -> float:
        raw_values = [response.headers.get("Retry-After")]
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                raw_values.append(error.get("retryAfter"))
                retry_ms = error.get("retryMs")
                if retry_ms not in (None, ""):
                    try:
                        return max(0.25, min(float(retry_ms) / 1000.0, 2.0))
                    except Exception:
                        pass
        for raw in raw_values:
            if raw in (None, ""):
                continue
            text = str(raw).strip()
            try:
                return max(0.25, min(float(text), 2.0))
            except Exception:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
                if match:
                    try:
                        return max(0.25, min(float(match.group(1)), 2.0))
                    except Exception:
                        continue
        return 1.0

    def _parse_fixture_odds(self, row: dict[str, Any], match: Match, bookmaker_slug: str) -> list[Offer]:
        bookmaker_odds = row.get("bookmakerOdds") or {}
        payload = bookmaker_odds.get(bookmaker_slug) or {}
        markets = payload.get("markets") or {}
        book_name = self._canonical_bookmaker(bookmaker_slug)
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()

        def add(family: str, selection: str, price: Any, point: float | None = None) -> None:
            try:
                odds = float(price)
            except Exception:
                return
            if odds <= 1.0:
                return
            key = (book_name, family, selection, point)
            if key in seen:
                return
            seen.add(key)
            offers.append(Offer(
                source="oddspapi",
                bookmaker=book_name,
                family=family,  # type: ignore[arg-type]
                selection=selection,
                price=odds,
                point=point,
                market_name="oddspapi",
                market_key=family,
                source_event_id=str(row.get("fixtureId") or ""),
                metadata={"oddspapi_bookmaker": bookmaker_slug},
            ))

        for market_key, market_value in markets.items():
            if not isinstance(market_value, dict):
                continue
            outcomes = market_value.get("outcomes") or {}
            parsed_h2h = False
            for outcome_key, outcome in outcomes.items():
                if not isinstance(outcome, dict):
                    continue
                players = outcome.get("players") or {}
                for player in players.values():
                    if not isinstance(player, dict):
                        continue
                    selection = self._resolve_h2h_selection(
                        match=match,
                        market_key=str(market_key or ""),
                        outcome_key=str(outcome_key or ""),
                        player=player,
                    )
                    if not selection:
                        continue
                    price = player.get("price")
                    add("h2h", selection, price)
                    parsed_h2h = True
            if parsed_h2h:
                continue
        return offers

    @staticmethod
    def _resolve_h2h_selection(match: Match, market_key: str, outcome_key: str, player: dict[str, Any]) -> str | None:
        market_key = str(market_key or "").strip()
        outcome_key = str(outcome_key or "").strip()
        player_name = str(player.get("playerName") or "").strip().lower()
        bookmaker_outcome_id = str(player.get("bookmakerOutcomeId") or "").strip().lower()

        if market_key == "101":
            direct = {
                "101": match.home_team,
                "102": "Draw",
                "103": match.away_team,
            }.get(outcome_key)
            if direct:
                return direct
            if bookmaker_outcome_id.endswith("101"):
                return match.home_team
            if bookmaker_outcome_id.endswith("102"):
                return "Draw"
            if bookmaker_outcome_id.endswith("103"):
                return match.away_team

        if player_name in {"home", "1"}:
            return match.home_team
        if player_name in {"away", "2"}:
            return match.away_team
        if player_name in {"draw", "x"}:
            return "Draw"
        return None

    def _build_match(self, row: dict[str, Any]) -> Match | None:
        home = str(row.get("participant1Name") or "").strip()
        away = str(row.get("participant2Name") or "").strip()
        league = str(row.get("tournamentName") or "").strip()
        fixture_id = str(row.get("fixtureId") or "").strip()
        if not home or not away or not fixture_id:
            return None
        if is_simulated_or_esports_event(home, away, league):
            return None
        try:
            start = parse_datetime(row.get("startTime"))
        except Exception:
            return None
        tier = "low" if self._looks_low_tier(league) else "mid"
        return Match(
            source="oddspapi",
            source_event_id=fixture_id,
            sport_key="soccer",
            league_name=league,
            home_team=home,
            away_team=away,
            commence_time=start,
            home_team_norm="",
            away_team_norm="",
            league_key="",
            tier=tier,
            metadata={
                "oddspapi_fixture_id": fixture_id,
                "oddspapi_tournament_id": str(row.get("tournamentId") or ""),
                "oddspapi_raw_fixture": row,
            },
        )

    def _match_fixture(self, row: dict[str, Any], matches: list[Match]) -> tuple[Match, str] | None:
        home = str(row.get("participant1Name") or "").strip()
        away = str(row.get("participant2Name") or "").strip()
        league = str(row.get("tournamentName") or "").strip()
        if not home or not away:
            return None
        try:
            start = parse_datetime(row.get("startTime"))
        except Exception:
            return None
        best_match = None
        best_score = 0.0
        best_quality = None
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
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
                best_quality = quality
                best_match = match
        if best_match is None or best_score < 48.0 or best_quality is None:
            return None
        return best_match, best_quality

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)

        def key(match: Match) -> tuple[int, float, str]:
            tier_rank = 0 if getattr(match, "tier", "mid") == "top" else 1 if getattr(match, "tier", "mid") == "mid" else 2
            kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
            return (tier_rank, kickoff_distance, match.league_name.lower())

        return sorted(matches, key=key)

    def _bookmaker_slugs(self) -> list[str]:
        source = list(getattr(self.settings, "oddspapi_bookmakers", []) or [])
        mapping = {
            "bet365": "bet365",
            "unibet": "unibet",
        }
        values: list[str] = []
        for item in source:
            slug = mapping.get(normalize_bookmaker_name(str(item or "")))
            if slug and slug not in values:
                values.append(slug)
        return values or ["bet365", "unibet"]

    @staticmethod
    def _canonical_bookmaker(name: str) -> str:
        norm = normalize_bookmaker_name(name)
        if norm == "bet365":
            return "Bet365"
        if norm.startswith("unibet"):
            return "Unibet"
        return str(name or "Unknown")

    @staticmethod
    def _chunked(values: list[str], size: int) -> list[list[str]]:
        size = max(1, int(size or 1))
        return [values[index:index + size] for index in range(0, len(values), size)]

    @staticmethod
    def _looks_low_tier(league_name: str) -> bool:
        text = str(league_name or "").lower()
        markers = ("u17", "u18", "u19", "u20", "u21", "u23", "women", "reserve", "friendly", "esports")
        return any(marker in text for marker in markers)

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "oddspapi_offers.json"

    def _load_cached_offers(self, matches: list[Match]) -> dict[str, list[Offer]] | None:
        path = self._cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        fetched_at = payload.get("fetched_at")
        try:
            fetched_dt = parse_datetime(fetched_at)
        except Exception:
            return None
        if datetime.now(UTC) - fetched_dt > timedelta(minutes=self.min_interval):
            return None
        wanted = {m.match_key for m in matches}
        out: dict[str, list[Offer]] = {}
        for match_key, rows in (payload.get("offers") or {}).items():
            if match_key not in wanted or not isinstance(rows, list):
                continue
            offers: list[Offer] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
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
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "offers": {k: [asdict(item) for item in v] for k, v in offers_by_match.items()},
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return
