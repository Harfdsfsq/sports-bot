from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import parse_datetime, score_event_match


class SStatsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = "https://api.sstats.net/Games/list"

    async def fetch_context(
        self,
        matches: list[Match],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_sstats_context),
            "api_key_present": bool(self.settings.sstats_api_key),
            "requests": 0,
            "response_errors": 0,
            "days_requested": 0,
            "rows_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
            "last_url": self.url,
        }
        preview: dict[str, Any] = {
            "sample_rows": [],
            "sample_contexts": [],
            "matched_examples": [],
            "unmatched_rows": [],
        }

        if not self.settings.enable_sstats_context:
            return {}, stats, preview
        if not self.settings.sstats_api_key:
            return {}, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC) - timedelta(days=1)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC) + timedelta(days=1)
        from_date = min_dt.date().isoformat()
        to_date = max_dt.date().isoformat()
        stats["days_requested"] = (max_dt.date() - min_dt.date()).days + 1

        rows: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            rows = await self._fetch_rows(client, from_date, to_date, stats)

        stats["rows_fetched"] = len(rows)
        if rows:
            preview["sample_rows"] = rows[:3]
        else:
            return {}, stats, preview

        contexts: dict[str, MatchContext] = {}
        for row in rows:
            event_home = self._extract_team_name(row, "home")
            event_away = self._extract_team_name(row, "away")
            if not event_home or not event_away:
                stats["unmatched_rows"] += 1
                continue

            event_league = self._extract_league_name(row)
            event_start = self._extract_start(row)
            if event_start is None:
                stats["unmatched_rows"] += 1
                continue

            best_match: Match | None = None
            best_score = 0.0
            best_quality: str | None = None

            for match in soccer_matches:
                score, quality = score_event_match(
                    sport=match.sport_key,
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=event_home,
                    event_away=event_away,
                    event_start=event_start,
                    event_league=event_league,
                    exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                    fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
                )
                if score > best_score:
                    best_match = match
                    best_score = score
                    best_quality = quality

            if best_match is None or best_score < 48.0:
                stats["unmatched_rows"] += 1
                if len(preview["unmatched_rows"]) < 10:
                    preview["unmatched_rows"].append(
                        {
                            "home": event_home,
                            "away": event_away,
                            "league": event_league,
                            "date": event_start.isoformat(),
                        }
                    )
                continue

            context = self._row_to_context(row)
            contexts[best_match.match_key] = context
            stats["contexts_built"] = len(contexts)

            if best_quality == "exact":
                stats["matched_exact"] += 1
            elif best_quality == "loose":
                stats["matched_loose"] += 1
            elif best_quality == "fuzzy":
                stats["matched_fuzzy"] += 1

            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append(
                    {
                        "match_key": best_match.match_key,
                        "match_home": best_match.home_team,
                        "match_away": best_match.away_team,
                        "row_home": event_home,
                        "row_away": event_away,
                        "league": event_league,
                        "quality": best_quality,
                        "score": round(best_score, 2),
                    }
                )
            if len(preview["sample_contexts"]) < 5:
                preview["sample_contexts"].append(
                    {
                        "match_key": best_match.match_key,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                    }
                )

        return contexts, stats, preview

    async def _fetch_rows(
        self,
        client: httpx.AsyncClient,
        from_date: str,
        to_date: str,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        params = {
            "from": from_date,
            "to": to_date,
            "limit": 1000,
            "apikey": self.settings.sstats_api_key,
        }

        stats["requests"] += 1
        try:
            response = await client.get(self.url, params=params)
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_body_preview"] = f"request failed: {exc}"
            return []

        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:2000]

        if response.status_code != 200:
            stats["response_errors"] += 1
            return []

        try:
            payload = response.json()
        except Exception:
            stats["response_errors"] += 1
            return []

        if isinstance(payload, dict):
            stats["payload_shapes"].append(",".join(sorted(payload.keys())[:12]))
            data = payload.get("data") or payload.get("results") or []
        elif isinstance(payload, list):
            stats["payload_shapes"].append("list")
            data = payload
        else:
            stats["payload_shapes"].append(type(payload).__name__)
            return []

        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]

    def _row_to_context(self, row: dict[str, Any]) -> MatchContext:
        expected_home = self._first_float(
            row,
            ["ExpectedGoalsHome", "xGHome", "CalculatedXgHome", "homeXg", "home_xg"],
        )
        expected_away = self._first_float(
            row,
            ["ExpectedGoalsAway", "xGAway", "CalculatedXgAway", "awayXg", "away_xg"],
        )

        if expected_home is None or expected_away is None:
            home_prob, away_prob = self._extract_win_probabilities(row)
            if expected_home is None and home_prob is not None:
                expected_home = home_prob * 2.4
            if expected_away is None and away_prob is not None:
                expected_away = away_prob * 2.4

        return MatchContext(
            source="sstats",
            payload=row,
            expected_home=expected_home,
            expected_away=expected_away,
        )

    def _extract_win_probabilities(self, row: dict[str, Any]) -> tuple[float | None, float | None]:
        markets = row.get("odds")
        if not isinstance(markets, list):
            return None, None

        selected: list[dict[str, Any]] | None = None
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = market.get("marketId")
            market_name = str(market.get("marketName") or "").strip().lower()
            odds = market.get("odds")
            if not isinstance(odds, list):
                continue
            if market_id == 1 or market_name in {"1x2", "match odds", "full time result"}:
                selected = [item for item in odds if isinstance(item, dict)]
                break

        if not selected:
            return None, None

        home_odds = None
        away_odds = None
        for item in selected:
            name = str(item.get("name") or "").strip().lower()
            value = self._to_float(item.get("value"))
            if value is None or value <= 1.0:
                continue
            if name == "home":
                home_odds = value
            elif name == "away":
                away_odds = value

        home_prob = (1.0 / home_odds) if home_odds else None
        away_prob = (1.0 / away_odds) if away_odds else None
        return home_prob, away_prob

    @staticmethod
    def _extract_team_name(row: dict[str, Any], side: str) -> str:
        nested_key = "homeTeam" if side == "home" else "awayTeam"
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            name = nested.get("name")
            if name:
                return str(name).strip()

        fallback_keys = (
            ["HomeTeam", "home", "home_name", "Home", "team_home", "homeTeamName"]
            if side == "home"
            else ["AwayTeam", "away", "away_name", "Away", "team_away", "awayTeamName"]
        )
        for key in fallback_keys:
            value = row.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_league_name(row: dict[str, Any]) -> str:
        season = row.get("season")
        if isinstance(season, dict):
            league = season.get("league")
            if isinstance(league, dict):
                name = league.get("name")
                if name:
                    return str(name).strip()

        for key in ["League", "league", "Tournament", "CompetitionName", "competition"]:
            value = row.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_start(row: dict[str, Any]) -> Any | None:
        for key in ["date", "Date", "GameStart", "StartTime", "datetime", "MatchDate"]:
            value = row.get(key)
            if value not in (None, ""):
                try:
                    return parse_datetime(str(value))
                except Exception:
                    continue
        return None

    @staticmethod
    def _first_float(payload: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            value = payload.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except Exception:
                continue
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None
