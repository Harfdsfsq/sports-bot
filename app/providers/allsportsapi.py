from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import normalize_bookmaker_name, parse_datetime, score_event_match


class AllSportsApiOddsProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = getattr(settings, "allsportsapi_api_key", None)
        self.base_url = str(getattr(settings, "allsportsapi_base_url", "https://apiv2.allsportsapi.com/football/")).rstrip("/")
        self.timeout = float(getattr(settings, "allsportsapi_timeout_seconds", 12.0) or 12.0)
        self.min_interval = max(30, int(getattr(settings, "allsportsapi_min_fetch_interval_minutes", 120) or 120))
        self.match_limit = max(1, int(getattr(settings, "allsportsapi_match_limit", 12) or 12))
        self._bootstrap_fixtures_cache: list[dict[str, Any]] = []

    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "matches_built": 0,
            "http_statuses": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": []}
        if not self.api_key:
            return [], stats, preview

        now = datetime.now(UTC)
        from_date = now.date().isoformat()
        to_date = (now + timedelta(days=max(1, int(getattr(self.settings, "run_days_ahead", 2) or 2)))).date().isoformat()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            payload = await self._get_json(
                client,
                {"met": "Fixtures", "APIkey": self.api_key, "from": from_date, "to": to_date, "timezone": "UTC"},
                stats,
            )
        rows = self._result_rows(payload)
        self._bootstrap_fixtures_cache = list(rows)
        stats["fixtures_fetched"] = len(rows)
        preview["sample_fixtures"] = rows[:3]
        matches: list[Match] = []
        seen: set[str] = set()
        for row in rows:
            home = str(row.get("event_home_team") or "").strip()
            away = str(row.get("event_away_team") or "").strip()
            league = str(row.get("league_name") or "").strip()
            if not home or not away:
                continue
            try:
                commence = parse_datetime(f"{row.get('event_date')}T{row.get('event_time')}:00+00:00")
            except Exception:
                continue
            key = f"{home.lower()}::{away.lower()}::{commence.isoformat()}"
            if key in seen:
                continue
            seen.add(key)
            matches.append(Match(
                source="allsportsapi",
                source_event_id=str(row.get("event_key") or row.get("match_id") or ""),
                sport_key="soccer",
                league_name=league,
                home_team=home,
                away_team=away,
                commence_time=commence,
                home_team_norm="",
                away_team_norm="",
                league_key="",
                tier="mid",
                metadata={"allsportsapi_fixture": row},
            ))
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
            for item in matches[:5]
        ]
        return matches, stats, preview

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
        from_date = min(m.commence_time for m in prioritized).astimezone(UTC).date().isoformat()
        to_date = max(m.commence_time for m in prioritized).astimezone(UTC).date().isoformat()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            fixtures = list(self._bootstrap_fixtures_cache) if self._bootstrap_fixtures_cache else []
            if not fixtures:
                fixtures_payload = await self._get_json(
                    client,
                    {"met": "Fixtures", "APIkey": self.api_key, "from": from_date, "to": to_date, "timezone": "UTC"},
                    stats,
                )
                fixtures = self._result_rows(fixtures_payload)
            stats["fixtures_fetched"] = len(fixtures)
            preview["sample_fixtures"] = fixtures[:3]

            mapping: dict[str, dict[str, Any]] = {}
            exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
            fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
            for row in fixtures:
                home = str(row.get("event_home_team") or "").strip()
                away = str(row.get("event_away_team") or "").strip()
                league = str(row.get("league_name") or "").strip()
                if not home or not away:
                    continue
                try:
                    start = parse_datetime(f"{row.get('event_date')}T{row.get('event_time')}:00+00:00")
                except Exception:
                    continue
                best_match = None
                best_score = 0.0
                best_quality = None
                for match in prioritized:
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
                if best_match is None or best_score < 48.0:
                    continue
                mapping[best_match.match_key] = {"match": best_match, "row": row}
                if best_quality == "exact":
                    stats["matched_exact"] += 1
                elif best_quality == "loose":
                    stats["matched_loose"] += 1
                elif best_quality == "fuzzy":
                    stats["matched_fuzzy"] += 1
            stats["events_matched"] = len(mapping)

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            for item in mapping.values():
                event_id = str(item["row"].get("event_key") or item["row"].get("match_id") or "")
                if not event_id:
                    continue
                odds_payload = await self._get_json(
                    client,
                    {"met": "Odds", "APIkey": self.api_key, "matchId": event_id},
                    stats,
                )
                rows = self._result_odds_rows(odds_payload, event_id)
                if rows and len(preview["sample_odds"]) < 2:
                    preview["sample_odds"].append(rows[0])
                parsed = self._parse_odds_rows(rows, item["match"], event_id)
                if parsed:
                    offers_by_match[item["match"].match_key].extend(parsed)
                    stats["offers_parsed"] += len(parsed)

        output = {k: v for k, v in offers_by_match.items() if v}
        self._write_cache(output)
        return output, stats, preview

    async def _get_json(self, client: httpx.AsyncClient, params: dict[str, Any], stats: dict[str, Any]) -> Any | None:
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}/", params=params)
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

    @staticmethod
    def _result_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and str(payload.get("success")) in {"1", "True", "true"}:
            result = payload.get("result")
            if isinstance(result, list):
                return [row for row in result if isinstance(row, dict)]
        return []

    @staticmethod
    def _result_odds_rows(payload: Any, event_id: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if str(payload.get("success")) not in {"1", "True", "true"}:
            return []
        result = payload.get("result")
        if isinstance(result, dict):
            rows = result.get(str(event_id))
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return []

    def _parse_odds_rows(self, rows: list[dict[str, Any]], match: Match, event_id: str) -> list[Offer]:
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()
        allowed = {
            self._canonical_bookmaker(x)
            for x in (
                getattr(self.settings, "allsportsapi_bookmakers", [])
                or ["Bet365", "Unibet", "Pinnacle", "Betfair", "William Hill", "1xBet", "Bwin"]
            )
        }

        def add(book: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None) -> None:
            try:
                odds = float(str(price).replace(",", "."))
            except Exception:
                return
            if odds <= 1.0:
                return
            bookmaker = self._canonical_bookmaker(book)
            if allowed and bookmaker not in allowed:
                return
            key = (bookmaker, family, selection, point)
            if key in seen:
                return
            seen.add(key)
            offers.append(Offer(
                source="allsportsapi",
                bookmaker=bookmaker,
                family=family,  # type: ignore[arg-type]
                selection=selection,
                price=odds,
                point=point,
                team_side=team_side,
                market_name=family,
                market_key=family,
                source_event_id=event_id,
                metadata={"allsportsapi_match_id": event_id},
            ))

        for row in rows:
            book = str(row.get("odd_bookmakers") or row.get("bookmaker") or "unknown")
            add(book, "h2h", match.home_team, row.get("odd_1"))
            add(book, "h2h", "Draw", row.get("odd_x"))
            add(book, "h2h", match.away_team, row.get("odd_2"))
            add(book, "doubleChance", "1X", row.get("odd_1x"))
            add(book, "doubleChance", "X2", row.get("odd_x2"))
            add(book, "doubleChance", "12", row.get("odd_12"))
            add(book, "btts", "Yes", row.get("bts_yes"))
            add(book, "btts", "No", row.get("bts_no"))

            for key, value in row.items():
                if value in (None, "", "null"):
                    continue
                low = str(key).strip().lower()
                m_total = re.match(r"([ou])\+?(-?\d+(?:\.\d+)?)$", low)
                if m_total:
                    selection = "Over" if m_total.group(1) == "o" else "Under"
                    point = float(m_total.group(2))
                    add(book, "totals", selection, value, point)
                    continue

                m_ah = re.match(r"ah([+-]?\d+(?:\.\d+)?)_(1|2)$", low)
                if m_ah:
                    handicap = float(m_ah.group(1))
                    side = m_ah.group(2)
                    if side == "1":
                        if abs(handicap) < 1e-9:
                            add(book, "dnb", match.home_team, value, 0.0, "home")
                        else:
                            add(book, "spreads", match.home_team, value, handicap, "home")
                    else:
                        if abs(handicap) < 1e-9:
                            add(book, "dnb", match.away_team, value, 0.0, "away")
                        else:
                            add(book, "spreads", match.away_team, value, -handicap, "away")
        return offers

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        def key(match: Match) -> tuple[int, float, str]:
            tier_rank = 0 if getattr(match, "tier", "mid") == "top" else 1 if getattr(match, "tier", "mid") == "mid" else 2
            kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
            return (tier_rank, kickoff_distance, match.league_name.lower())
        return sorted(matches, key=key)

    @staticmethod
    def _canonical_bookmaker(name: str) -> str:
        norm = normalize_bookmaker_name(name)
        if norm == "bet365":
            return "Bet365"
        if norm.startswith("unibet"):
            return "Unibet"
        if norm == "pinnacle":
            return "Pinnacle"
        if norm == "betfair":
            return "Betfair"
        if norm in {"williamhill", "williamhillsportsbook"}:
            return "William Hill"
        if norm in {"1xbet", "xbet"}:
            return "1xBet"
        if norm == "bwin":
            return "Bwin"
        return str(name or "Unknown")

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "allsportsapi_offers.json"

    def _load_cached_offers(self, matches: list[Match]) -> dict[str, list[Offer]] | None:
        path = self._cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        try:
            fetched_dt = parse_datetime(payload.get("fetched_at"))
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
