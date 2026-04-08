from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, normalize_probability_percent, parse_datetime, score_event_match


class ApiFootballContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = os.getenv("API_FOOTBALL_KEY") or getattr(settings, "api_football_key", None)
        self.base_url = os.getenv("API_FOOTBALL_BASE_URL") or getattr(settings, "api_football_base_url", None) or "https://v3.football.api-sports.io"

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "contexts_built": 0,
            "prediction_requests_limited_to": 0,
            "rate_limited": False,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "http_statuses": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_fixtures": [], "sample_predictions": []}
        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        days = max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4))
        soccer_matches = self._prioritize_matches(soccer_matches)
        limit = self._prediction_limit(days)
        if limit and len(soccer_matches) > limit:
            soccer_matches = soccer_matches[:limit]
            stats["prediction_requests_limited_to"] = limit

        headers = {"x-apisports-key": self.api_key}
        now = datetime.now(UTC)
        fixtures: list[dict[str, Any]] = []
        match_dates = sorted({ensure_date.astimezone(UTC).date().isoformat() for ensure_date in [m.commence_time for m in soccer_matches]})
        if bool(getattr(self.settings, "api_football_fetch_match_dates_only", True)) and match_dates:
            free_ahead = max(0, int(getattr(self.settings, "api_football_free_days_ahead", 1) or 1))
            max_allowed = (now + timedelta(days=free_ahead)).date()
            date_list = [day for day in match_dates if datetime.fromisoformat(day).date() <= max_allowed]
            if not date_list:
                date_list = [(now + timedelta(days=offset)).date().isoformat() for offset in range(min(days + 1, free_ahead + 1))]
        else:
            date_list = [(now + timedelta(days=offset)).date().isoformat() for offset in range(days + 1)]

        async with httpx.AsyncClient(timeout=25.0) as client:
            for day in date_list:
                stats["requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}/fixtures", headers=headers, params={"date": day, "timezone": "UTC"})
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"fixtures request failed: {exc}"
                    continue
                stats["http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:1200]
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue
                payload = self._safe_json(response)
                if self._has_plan_error(payload):
                    stats["response_errors"] += 1
                    break
                rows = self._response_rows(payload)
                fixtures.extend(rows)
                if offset == 0 and rows:
                    preview["sample_fixtures"] = rows[:3]
            stats["fixtures_fetched"] = len(fixtures)

            contexts: dict[str, MatchContext] = {}
            for match in soccer_matches:
                if stats.get("rate_limited"):
                    break
                fixture, quality = self._match_fixture(match, fixtures)
                if fixture is None:
                    continue
                fixture_id = (((fixture.get("fixture") or {}).get("id")))
                if not fixture_id:
                    continue
                stats["requests"] += 1
                try:
                    response = await client.get(f"{self.base_url}/predictions", headers=headers, params={"fixture": fixture_id})
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"predictions request failed: {exc}"
                    continue
                stats["http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:1200]
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue
                payload = self._safe_json(response)
                if self._has_rate_limit_error(payload):
                    stats["response_errors"] += 1
                    stats["rate_limited"] = True
                    break
                rows = self._response_rows(payload)
                if not rows:
                    continue
                if len(preview["sample_predictions"]) < 3:
                    preview["sample_predictions"].append(rows[0])
                contexts[match.match_key] = self._prediction_to_context(rows[0], fixture)
                stats["contexts_built"] += 1
                if quality == "exact":
                    stats["matched_exact"] += 1
                elif quality == "loose":
                    stats["matched_loose"] += 1
                elif quality == "fuzzy":
                    stats["matched_fuzzy"] += 1

        return contexts, stats, preview

    def _prediction_limit(self, days: int) -> int:
        raw = os.getenv("API_FOOTBALL_PREDICTIONS_LIMIT") or getattr(self.settings, "api_football_predictions_limit", None)
        safe_default = max(1, 10 - (days + 1))
        try:
            value = int(raw) if raw not in (None, "") else safe_default
        except Exception:
            value = safe_default
        return max(1, min(value, safe_default))

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        def key(match: Match) -> tuple[int, float, str, str]:
            tier_rank = 0 if getattr(match, "tier", "mid") == "top" else 1 if getattr(match, "tier", "mid") == "mid" else 2
            kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
            return (tier_rank, kickoff_distance, match.league_name.lower(), match.home_team.lower())
        return sorted(matches, key=key)

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    @staticmethod
    def _response_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            rows = payload.get("response")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return []

    def _match_fixture(self, match: Match, fixtures: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        for row in fixtures:
            teams = row.get("teams") or {}
            league = row.get("league") or {}
            fixture = row.get("fixture") or {}
            home = str((teams.get("home") or {}).get("name") or "")
            away = str((teams.get("away") or {}).get("name") or "")
            try:
                start = parse_datetime(fixture.get("date"))
            except Exception:
                continue
            score, quality = score_event_match(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=home,
                event_away=away,
                event_start=start,
                event_league=str(league.get("name") or ""),
                exact_tolerance_hours=float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
                fuzzy_tolerance_hours=float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8),
            )
            if score > best_score:
                best_score = score
                best_quality = quality
                best = row
        return (best, best_quality) if best_score >= 48.0 else (None, None)

    @staticmethod
    def _has_rate_limit_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if isinstance(errors, dict):
            return bool(errors.get("rateLimit"))
        return False

    @staticmethod
    def _has_plan_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if isinstance(errors, dict):
            return bool(errors.get("plan"))
        return False

    def _prediction_to_context(self, row: dict[str, Any], fixture: dict[str, Any]) -> MatchContext:
        preds = row.get("predictions") or row.get("prediction") or {}
        percent = preds.get("percent") or {}
        goals = preds.get("goals") or {}
        home_prob = normalize_probability_percent(percent.get("home") or percent.get("Home"))
        draw_prob = normalize_probability_percent(percent.get("draw") or percent.get("Draw"))
        away_prob = normalize_probability_percent(percent.get("away") or percent.get("Away"))
        expected_home = self._to_float(goals.get("home") or goals.get("home_goals"))
        expected_away = self._to_float(goals.get("away") or goals.get("away_goals"))
        confidence = 59.0
        if home_prob is not None and away_prob is not None:
            confidence += 5.0
        if expected_home is not None and expected_away is not None:
            confidence += 4.0
        confidence = clamp(confidence, 58.0, 76.0)
        return MatchContext(
            source="api_football",
            payload={"prediction": row, "fixture": fixture},
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "api_football_draw_probability": draw_prob,
                "api_football_advice": preds.get("advice"),
                "api_football_under_over": preds.get("under_over"),
            },
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(str(value).replace(",", "."))
        except Exception:
            return None
