from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_league_name, parse_datetime, score_event_match, to_decimal_probability


class SStatsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.primary_url = "https://api.sstats.net/Games/list"
        self.fallback_url = "https://api.sstats.net/games/list"

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_sstats_context and self.settings.sstats_api_key),
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
            "last_url": None,
        }
        preview: dict[str, Any] = {"unmatched_rows": [], "matched_examples": [], "request_debug": []}
        if not self.settings.enable_sstats_context or not self.settings.sstats_api_key:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        by_date: dict[str, list[Match]] = defaultdict(list)
        for match in soccer_matches:
            by_date[match.commence_time.date().isoformat()].append(match)

        contexts: dict[str, MatchContext] = {}
        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            for date_key, items in by_date.items():
                stats["days_requested"] += 1
                rows = await self._fetch_day(client, date_key, stats)
                stats["rows_fetched"] += len(rows)
                day_contexts, day_stats, day_preview = self._match_rows_to_contexts(date_key, rows, items)
                contexts.update(day_contexts)
                stats["contexts_built"] = len(contexts)
                stats["matched_exact"] += day_stats["matched_exact"]
                stats["matched_loose"] += day_stats["matched_loose"]
                stats["matched_fuzzy"] += day_stats["matched_fuzzy"]
                stats["unmatched_rows"] += day_stats["unmatched_rows"]
                preview["unmatched_rows"].extend(day_preview["unmatched_rows"][:10])
                preview["matched_examples"].extend(day_preview["matched_examples"][:10])
                if stats.get("last_url"):
                    preview["request_debug"].append({
                        "date": date_key,
                        "url": stats.get("last_url"),
                        "status": (stats.get("http_statuses") or [None])[-1],
                        "shape": (stats.get("payload_shapes") or [None])[-1],
                    })
        return contexts, stats, preview

    async def _fetch_day(self, client: httpx.AsyncClient, date_key: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        params = {
            "from": date_key,
            "to": date_key,
            "limit": 1000,
            "apikey": self.settings.sstats_api_key,
        }
        headers = {"apikey": self.settings.sstats_api_key}
        stats["requests"] += 1
        for url in [self.primary_url, self.fallback_url, self.primary_url + "/", self.fallback_url + "/"]:
            stats["last_url"] = url
            try:
                response = await client.get(url, params=params, headers=headers)
            except Exception:
                stats["response_errors"] += 1
                continue
            stats.setdefault("http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:400]
            if response.status_code != 200:
                stats["response_errors"] += 1
                continue
            try:
                payload = response.json()
            except Exception:
                stats["response_errors"] += 1
                continue
            if isinstance(payload, list):
                stats.setdefault("payload_shapes", []).append("list")
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict):
                stats.setdefault("payload_shapes", []).append(",".join(sorted(payload.keys())[:10]))
                rows = (
                    payload.get("data")
                    or payload.get("results")
                    or payload.get("rows")
                    or payload.get("games")
                    or payload.get("matches")
                    or payload.get("items")
                    or []
                )
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            return []
        return []

    def _match_rows_to_contexts(
        self,
        date_key: str,
        rows: list[dict[str, Any]],
        matches: list[Match],
    ) -> tuple[dict[str, MatchContext], dict[str, int], dict[str, Any]]:
        stats = {"matched_exact": 0, "matched_loose": 0, "matched_fuzzy": 0, "unmatched_rows": 0}
        preview = {"unmatched_rows": [], "matched_examples": []}
        best_for_match: dict[str, tuple[float, MatchContext]] = {}
        for row in rows:
            home = self._first_text(row, ["HomeTeam", "home", "home_name", "Home", "team_home", "homeTeamName"])
            away = self._first_text(row, ["AwayTeam", "away", "away_name", "Away", "team_away", "awayTeamName"])
            league = self._first_text(row, ["League", "league", "Tournament", "CompetitionName", "competition"])
            raw_date = self._first_text(row, ["Date", "date", "GameStart", "StartTime", "datetime", "MatchDate"]) or f"{date_key}T12:00:00+00:00"
            try:
                row_start = parse_datetime(raw_date)
            except Exception:
                row_start = datetime.fromisoformat(f"{date_key}T12:00:00+00:00").astimezone(UTC)
            best_score = 0.0
            best_mode: str | None = None
            best_match: Match | None = None
            for match in matches:
                score, mode = score_event_match(
                    sport="soccer",
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=home,
                    event_away=away,
                    event_start=row_start,
                    event_league=league,
                    exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                    fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
                )
                if score > best_score:
                    best_score = score
                    best_mode = mode
                    best_match = match
            if best_match is None or best_mode is None:
                stats["unmatched_rows"] += 1
                preview["unmatched_rows"].append(
                    {
                        "home": home,
                        "away": away,
                        "league": league,
                        "date": raw_date,
                    }
                )
                continue
            context = self._row_to_context(row)
            previous = best_for_match.get(best_match.match_key)
            if previous is not None and previous[0] >= best_score:
                continue
            best_for_match[best_match.match_key] = (best_score, context)
            if best_mode == "exact":
                stats["matched_exact"] += 1
            elif best_mode == "loose":
                stats["matched_loose"] += 1
            else:
                stats["matched_fuzzy"] += 1
            preview["matched_examples"].append(
                {
                    "match_key": best_match.match_key,
                    "home": home,
                    "away": away,
                    "league": league,
                    "mode": best_mode,
                    "score": round(best_score, 2),
                    "xg_home": context.expected_home,
                    "xg_away": context.expected_away,
                }
            )
        return {key: value for key, (_score, value) in best_for_match.items()}, stats, preview

    def _row_to_context(self, row: dict[str, Any]) -> MatchContext:
        expected_home = self._first_float(row, ["ExpectedGoalsHome", "xGHome", "CalculatedXgHome", "homeXg"])
        expected_away = self._first_float(row, ["ExpectedGoalsAway", "xGAway", "CalculatedXgAway", "awayXg"])
        home_win = to_decimal_probability(self._first_float(row, ["GlickoWinProbHome", "HomeWinProb", "ProbHome"]))
        away_win = to_decimal_probability(self._first_float(row, ["GlickoWinProbAway", "AwayWinProb", "ProbAway"]))
        home_starting = self._first_int(row, ["StartingHome", "HomeStarting", "Starting11Home", "StartingPlayersHome"])
        away_starting = self._first_int(row, ["StartingAway", "AwayStarting", "Starting11Away", "StartingPlayersAway"])
        confidence = 58.0
        if expected_home is not None and expected_away is not None:
            confidence += 8.0
        if home_win is not None and away_win is not None:
            confidence += 4.0
        if home_starting is not None and away_starting is not None:
            confidence += 2.0
        return MatchContext(
            source="sstats",
            payload=row,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_win,
            away_win_probability=away_win,
            home_starting=home_starting,
            away_starting=away_starting,
            confidence=min(confidence, 82.0),
            details={
                "league_key": canonicalize_league_name(self._first_text(row, ["League", "league", "Tournament", "CompetitionName"]))
            },
        )

    @staticmethod
    def _first_text(payload: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

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
    def _first_int(payload: dict[str, Any], keys: list[str]) -> int | None:
        for key in keys:
            value = payload.get(key)
            if value is None or value == "":
                continue
            try:
                return int(value)
            except Exception:
                continue
        return None
