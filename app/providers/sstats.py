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
            "last_url": "https://api.sstats.net/Games/list",
        }
        preview: dict[str, Any] = {"sample_rows": [], "sample_contexts": []}
        contexts: dict[str, MatchContext] = {}

        if not self.settings.enable_sstats_context:
            return contexts, stats, preview

        if not self.settings.sstats_api_key:
            return contexts, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return contexts, stats, preview

        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC) - timedelta(days=1)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC) + timedelta(days=1)
        from_date = min_dt.date().isoformat()
        to_date = max_dt.date().isoformat()

        stats["days_requested"] = (max_dt.date() - min_dt.date()).days + 1

        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            rows, fetch_meta = await self._fetch_games_list(client, from_date, to_date)
            stats["requests"] += 1
            stats["http_statuses"].extend(fetch_meta["http_statuses"])
            stats["payload_shapes"].extend(fetch_meta["payload_shapes"])
            stats["last_body_preview"] = fetch_meta["last_body_preview"]

            if fetch_meta["response_error"]:
                stats["response_errors"] += 1
                return contexts, stats, preview

            stats["rows_fetched"] = len(rows)
            if rows:
                preview["sample_rows"] = rows[:3]

            by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                row_date = self._extract_row_date(row)
                if row_date:
                    by_date[row_date].append(row)

            for match in soccer_matches:
                candidates: list[dict[str, Any]] = []
                match_day = match.commence_time.astimezone(UTC).date()

                for offset in (-1, 0, 1):
                    day_key = (match_day + timedelta(days=offset)).isoformat()
                    candidates.extend(by_date.get(day_key, []))

                best_row, quality = self._match_row(match, candidates)
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

                context = MatchContext(
                    source="sstats",
                    payload=best_row,
                    expected_home=expected_home,
                    expected_away=expected_away,
                )
                contexts[match.match_key] = context
                stats["contexts_built"] += 1
                if quality == "exact":
                    stats["matched_exact"] += 1
                elif quality == "loose":
                    stats["matched_loose"] += 1
                elif quality == "fuzzy":
                    stats["matched_fuzzy"] += 1

                if len(preview["sample_contexts"]) < 3:
                    preview["sample_contexts"].append(
                        {
                            "match_key": match.match_key,
                            "home_team": match.home_team,
                            "away_team": match.away_team,
                            "quality": quality,
                            "expected_home": expected_home,
                            "expected_away": expected_away,
                        }
                    )

        return contexts, stats, preview

    async def _fetch_games_list(
        self,
        client: httpx.AsyncClient,
        from_date: str,
        to_date: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        meta = {
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
            "response_error": False,
        }

        params = {
            "from": from_date,
            "to": to_date,
            "limit": 1000,
            "apikey": self.settings.sstats_api_key,
        }

        try:
            response = await client.get("https://api.sstats.net/Games/list", params=params)
        except Exception as exc:
            meta["response_error"] = True
            meta["last_body_preview"] = f"request failed: {exc}"
            return [], meta

        meta["http_statuses"].append(response.status_code)
        meta["last_body_preview"] = response.text[:2000]

        if response.status_code != 200:
            meta["response_error"] = True
            return [], meta

        try:
            payload = response.json()
        except Exception:
            meta["response_error"] = True
            return [], meta

        if isinstance(payload, dict):
            meta["payload_shapes"].append(",".join(sorted(payload.keys())[:12]))
            data = payload.get("data") or payload.get("results") or []
        elif isinstance(payload, list):
            meta["payload_shapes"].append("list")
            data = payload
        else:
            meta["payload_shapes"].append(type(payload).__name__)
            data = []

        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)], meta
        return [], meta

    def _match_row(self, match: Match, rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
        best_row: dict[str, Any] | None = None
        best_score = -1
        best_quality: str | None = None

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
            quality = "fuzzy"

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

            if score >= 10:
                quality = "exact"
            elif score >= 8:
                quality = "loose"

            if score > best_score:
                best_score = score
                best_row = row
                best_quality = quality

        if best_score >= 6:
            return best_row, best_quality
        return None, None

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
