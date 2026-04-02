from __future__ import annotations

from collections import defaultdict
from datetime import UTC, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext


class SStatsContextProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch_context(self, matches: list[Match]) -> dict[str, MatchContext]:
        if not self.settings.sstats_api_key:
            return {}

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}

        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC) - timedelta(days=1)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC) + timedelta(days=1)

        from_date = min_dt.date().isoformat()
        to_date = max_dt.date().isoformat()

        rows: list[dict[str, Any]] = []
        contexts: dict[str, MatchContext] = {}

        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            rows = await self._fetch_games_list(client, from_date, to_date)
            if not rows:
                return {}

            by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                row_date = self._extract_row_date(row)
                if row_date:
                    by_date[row_date].append(row)

            for match in soccer_matches:
                candidates = []
                match_day = match.commence_time.astimezone(UTC).date()

                for offset in (-1, 0, 1):
                    day_key = (match_day + timedelta(days=offset)).isoformat()
                    candidates.extend(by_date.get(day_key, []))

                best_row = self._match_row(match, candidates)
                if not best_row:
                    continue

                expected_home = self._to_float(
                    self._first_value(
                        best_row,
                        [
                            "xGHome",
                            "ExpectedGoalsHome",
                            "home_xg",
                            "homeXg",
                            "homeXG",
                            "team1_xg",
                        ],
                    )
                )
                expected_away = self._to_float(
                    self._first_value(
                        best_row,
                        [
                            "xGAway",
                            "ExpectedGoalsAway",
                            "away_xg",
                            "awayXg",
                            "awayXG",
                            "team2_xg",
                        ],
                    )
                )

                if expected_home is None and expected_away is None:
                    continue

                contexts[match.match_key] = MatchContext(
                    source="sstats",
                    payload=best_row,
                    expected_home=expected_home,
                    expected_away=expected_away,
                )

        return contexts

    async def _fetch_games_list(
        self,
        client: httpx.AsyncClient,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        params = {
            "from": from_date,
            "to": to_date,
            "limit": 1000,
            "apikey": self.settings.sstats_api_key,
        }

        response = await client.get("https://api.sstats.net/Games/list", params=params)
        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except Exception:
            return []

        data = payload.get("data") or payload.get("results") or []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def _match_row(self, match: Match, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best_row: dict[str, Any] | None = None
        best_score = -1

        match_home = self._norm(match.home_team)
        match_away = self._norm(match.away_team)

        for row in rows:
            row_home = self._norm(
                self._first_value(
                    row,
                    [
                        "HomeTeam",
                        "home",
                        "homeTeam",
                        "team1",
                        "team_home",
                    ],
                )
            )
            row_away = self._norm(
                self._first_value(
                    row,
                    [
                        "AwayTeam",
                        "away",
                        "awayTeam",
                        "team2",
                        "team_away",
                    ],
                )
            )

            if not row_home or not row_away:
                continue

            score = 0

            if row_home == match_home:
                score += 4
            elif row_home in match_home or match_home in row_home:
                score += 2

            if row_away == match_away:
                score += 4
            elif row_away in match_away or match_away in row_away:
                score += 2

            row_date = self._extract_row_date(row)
            if row_date == match.commence_time.date().isoformat():
                score += 2

            if score > best_score:
                best_score = score
                best_row = row

        return best_row if best_score >= 6 else None

    @staticmethod
    def _extract_row_date(row: dict[str, Any]) -> str | None:
        raw = (
            row.get("date")
            or row.get("Date")
            or row.get("start")
            or row.get("starts_at")
            or row.get("kickoff")
            or row.get("time")
        )
        if raw is None:
            return None

        text = str(raw).strip()
        if not text:
            return None

        if "T" in text:
            return text.split("T", 1)[0]
        if " " in text:
            return text.split(" ", 1)[0]
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return None

    @staticmethod
    def _first_value(row: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                return row.get(key)
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _norm(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = text.replace("fc ", "").replace(" fc", "")
        text = text.replace(".", " ").replace("-", " ")
        return " ".join(text.split())
