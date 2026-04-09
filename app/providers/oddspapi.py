from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import normalize_bookmaker_name, parse_datetime, score_event_match


class OddsPapiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = getattr(settings, "oddspapi_api_key", None)
        self.base_url = str(getattr(settings, "oddspapi_base_url", "https://api.oddspapi.io/v4")).rstrip("/")
        self.timeout = float(getattr(settings, "oddspapi_timeout_seconds", 12.0) or 12.0)
        self.min_interval = max(30, int(getattr(settings, "oddspapi_min_fetch_interval_minutes", 360) or 360))
        self.match_limit = max(1, int(getattr(settings, "oddspapi_match_limit", 16) or 16))
        self.tournament_limit = max(1, int(getattr(settings, "oddspapi_tournament_limit", 4) or 4))

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "cache_hit": False,
            "requests": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "events_matched": 0,
            "offers_parsed": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "http_statuses": [],
            "last_body_preview": None,
            "families_supported": ["h2h"],
            "bookmakers_requested": [],
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

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            for bookmaker_slug in bookmakers:
                odds_rows = await self._get_json(
                    client,
                    "/odds-by-tournaments",
                    stats,
                    params={
                        "tournamentIds": ",".join(tournament_ids),
                        "bookmaker": bookmaker_slug,
                        "apiKey": self.api_key,
                    },
                )
                rows = [row for row in odds_rows if isinstance(row, dict)] if isinstance(odds_rows, list) else []
                if rows and len(preview["sample_odds"]) < 2:
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
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}{path}", params=params)
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_body_preview"] = f"request failed: {exc}"
            return None
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1800]
        if response.status_code != 200:
            stats["response_errors"] += 1
            return None
        try:
            return response.json()
        except Exception:
            stats["response_errors"] += 1
            return None

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

        for market_value in markets.values():
            if not isinstance(market_value, dict):
                continue
            outcomes = market_value.get("outcomes") or {}
            parsed_h2h = False
            for outcome in outcomes.values():
                if not isinstance(outcome, dict):
                    continue
                players = outcome.get("players") or {}
                for player in players.values():
                    if not isinstance(player, dict):
                        continue
                    label = str(player.get("bookmakerOutcomeId") or "").strip().lower()
                    price = player.get("price")
                    if label == "home":
                        add("h2h", match.home_team, price)
                        parsed_h2h = True
                    elif label == "away":
                        add("h2h", match.away_team, price)
                        parsed_h2h = True
                    elif label == "draw":
                        add("h2h", "Draw", price)
                        parsed_h2h = True
            if parsed_h2h:
                continue
        return offers

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
