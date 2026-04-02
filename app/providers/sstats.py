from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_league_name, parse_datetime, score_event_match


class SStatsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.primary_url = "https://api.sstats.net/Games/list"
        self.fallback_url = "https://api.sstats.net/games/list"

    async def fetch_context(
        self,
        matches: list[Match],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
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
        preview: dict[str, Any] = {
            "unmatched_rows": [],
            "matched_examples": [],
            "request_debug": [],
            "sample_rows": [],
            "sample_contexts": [],
        }

        if not self.settings.enable_sstats_context or not self.settings.sstats_api_key:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        min_dt = min(match.commence_time for match in soccer_matches).astimezone(UTC) - timedelta(days=1)
        max_dt = max(match.commence_time for match in soccer_matches).astimezone(UTC) + timedelta(days=1)
        date_keys = []
        current_day = min_dt.date()
        end_day = max_dt.date()
        while current_day <= end_day:
            date_keys.append(current_day.isoformat())
            current_day += timedelta(days=1)

        by_date: dict[str, list[Match]] = defaultdict(list)
        for match in soccer_matches:
            base = match.commence_time.astimezone(UTC).date()
            for offset in (-1, 0, 1):
                by_date[(base + timedelta(days=offset)).isoformat()].append(match)

        contexts: dict[str, MatchContext] = {}

        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            for date_key in date_keys:
                stats["days_requested"] += 1
                rows = await self._fetch_day(client, date_key, stats)
                stats["rows_fetched"] += len(rows)

                if rows and len(preview["sample_rows"]) < 3:
                    preview["sample_rows"].extend(rows[: 3 - len(preview["sample_rows"])])

                day_matches = by_date.get(date_key, [])
                if not day_matches or not rows:
                    if stats.get("last_url"):
                        preview["request_debug"].append(
                            {
                                "date": date_key,
                                "url": stats.get("last_url"),
                                "status": (stats.get("http_statuses") or [None])[-1],
                                "shape": (stats.get("payload_shapes") or [None])[-1],
                                "rows": len(rows),
                            }
                        )
                    continue

                day_contexts, day_stats, day_preview = self._match_rows_to_contexts(date_key, rows, day_matches)
                contexts.update(day_contexts)

                stats["contexts_built"] = len(contexts)
                stats["matched_exact"] += day_stats["matched_exact"]
                stats["matched_loose"] += day_stats["matched_loose"]
                stats["matched_fuzzy"] += day_stats["matched_fuzzy"]
                stats["unmatched_rows"] += day_stats["unmatched_rows"]

                preview["unmatched_rows"].extend(day_preview["unmatched_rows"][:10])
                preview["matched_examples"].extend(day_preview["matched_examples"][:10])
                preview["sample_contexts"].extend(day_preview["sample_contexts"][:10])

                if stats.get("last_url"):
                    preview["request_debug"].append(
                        {
                            "date": date_key,
                            "url": stats.get("last_url"),
                            "status": (stats.get("http_statuses") or [None])[-1],
                            "shape": (stats.get("payload_shapes") or [None])[-1],
                            "rows": len(rows),
                        }
                    )

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
            except Exception as exc:
                stats["response_errors"] += 1
                stats["last_body_preview"] = f"request failed: {exc}"
                continue

            stats.setdefault("http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:2000]

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
        preview = {"unmatched_rows": [], "matched_examples": [], "sample_contexts": []}
        best_for_match: dict[str, tuple[float, MatchContext]] = {}

        for row in rows:
            home = self._extract_team_name(row, "home")
            away = self._extract_team_name(row, "away")
            league = self._extract_league_name(row)
            raw_date = self._extract_date_text(row) or f"{date_key}T12:00:00+00:00"

            try:
                row_start = parse_datetime(raw_date)
            except Exception:
                row_start = datetime.fromisoformat(f"{date_key}T12:00:00+00:00").replace(tzinfo=UTC)

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
                if len(preview["unmatched_rows"]) < 10:
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

            example = {
                "match_key": best_match.match_key,
                "home": home,
                "away": away,
                "league": league,
                "mode": best_mode,
                "score": round(best_score, 2),
                "xg_home": context.expected_home,
                "xg_away": context.expected_away,
                "home_win_probability": context.home_win_probability,
                "away_win_probability": context.away_win_probability,
            }
            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append(example)
            if len(preview["sample_contexts"]) < 10:
                preview["sample_contexts"].append(example)

        return {key: value for key, (_score, value) in best_for_match.items()}, stats, preview

    def _row_to_context(self, row: dict[str, Any]) -> MatchContext:
        expected_home = self._first_float(
            row,
            ["ExpectedGoalsHome", "xGHome", "CalculatedXgHome", "homeXg", "home_xg"],
        )
        expected_away = self._first_float(
            row,
            ["ExpectedGoalsAway", "xGAway", "CalculatedXgAway", "awayXg", "away_xg"],
        )

        # Fallback: derive basic win probabilities from nested SStats odds market if xG is missing.
        home_win_probability, away_win_probability = self._extract_win_probabilities(row)

        confidence = 58.0
        if expected_home is not None and expected_away is not None:
            confidence += 8.0
        if home_win_probability is not None and away_win_probability is not None:
            confidence += 4.0

        return MatchContext(
            source="sstats",
            payload=row,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_win_probability,
            away_win_probability=away_win_probability,
            confidence=min(confidence, 82.0),
            details={
                "league_key": canonicalize_league_name(self._extract_league_name(row)),
                "row_home": self._extract_team_name(row, "home"),
                "row_away": self._extract_team_name(row, "away"),
                "row_date": self._extract_date_text(row),
                "odds_market_found": bool(self._find_match_odds_market(row)),
            },
        )

    def _extract_win_probabilities(self, row: dict[str, Any]) -> tuple[float | None, float | None]:
        market = self._find_match_odds_market(row)
        if not market:
            return None, None

        home_odds = None
        away_odds = None
        for item in market:
            if not isinstance(item, dict):
                continue
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
    def _find_match_odds_market(row: dict[str, Any]) -> list[dict[str, Any]] | None:
        markets = row.get("odds")
        if not isinstance(markets, list):
            return None

        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = market.get("marketId")
            market_name = str(market.get("marketName") or "").lower()
            items = market.get("odds")
            if not isinstance(items, list):
                continue
            if market_id == 1 or market_name in {"1x2", "match odds", "full time result"}:
                return [item for item in items if isinstance(item, dict)]
        return None

    @staticmethod
    def _extract_team_name(row: dict[str, Any], side: str) -> str:
        if side == "home":
            nested = row.get("homeTeam")
            if isinstance(nested, dict):
                name = nested.get("name")
                if name:
                    return str(name).strip()
            for key in ["HomeTeam", "home", "home_name", "Home", "team_home", "homeTeamName"]:
                value = row.get(key)
                if value:
                    return str(value).strip()
        else:
            nested = row.get("awayTeam")
            if isinstance(nested, dict):
                name = nested.get("name")
                if name:
                    return str(name).strip()
            for key in ["AwayTeam", "away", "away_name", "Away", "team_away", "awayTeamName"]:
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
    def _extract_date_text(row: dict[str, Any]) -> str:
        for key in ["Date", "date", "GameStart", "StartTime", "datetime", "MatchDate"]:
            value = row.get(key)
            if value:
                return str(value).strip()
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
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None
