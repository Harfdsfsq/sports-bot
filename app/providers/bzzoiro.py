from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, normalize_probability_percent, parse_datetime, score_event_match_variants
from app.utils import over_probability_from_lambda


class BzzoiroContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://sports.bzzoiro.com/api"
        self.api_key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "predictions_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_predictions": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_predictions": []}

        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        headers = {"Authorization": f"Token {self.api_key}"}
        min_dt = min(match.commence_time for match in soccer_matches).astimezone(UTC)
        max_dt = max(match.commence_time for match in soccer_matches).astimezone(UTC)
        date_from = min_dt.date().isoformat()
        date_to = max_dt.date().isoformat()

        async with httpx.AsyncClient(timeout=25.0) as client:
            events = await self._fetch_paginated_rows(
                client,
                "/events/",
                headers=headers,
                params={"date_from": date_from, "date_to": date_to, "tz": "UTC"},
                stats=stats,
            )
            stats["events_fetched"] = len(events)
            if events:
                preview["sample_events"] = events[:3]

            predictions = await self._fetch_paginated_rows(
                client,
                "/predictions/",
                headers=headers,
                params={"upcoming": "true", "date_from": date_from, "date_to": date_to, "tz": "UTC"},
                stats=stats,
            )
            stats["predictions_fetched"] = len(predictions)
            if predictions:
                preview["sample_predictions"] = predictions[:3]

        contexts: dict[str, MatchContext] = {}
        for match in soccer_matches:
            prediction, quality = self._match_prediction(match, predictions)
            if prediction is None:
                stats["unmatched_predictions"] += 1
                continue
            event = self._prediction_event(prediction, events)
            if event is None:
                event, _ = self._match_event(match, events)
            context = self._prediction_to_context(prediction, event, quality)
            contexts[match.match_key] = context
            stats["contexts_built"] += 1
            if quality == "exact":
                stats["matched_exact"] += 1
            elif quality == "loose":
                stats["matched_loose"] += 1
            elif quality == "fuzzy":
                stats["matched_fuzzy"] += 1

        return contexts, stats, preview

    async def _safe_get(self, client: httpx.AsyncClient, url: str, *, headers: dict[str, str], params: dict[str, Any]) -> httpx.Response | None:
        try:
            return await client.get(url, headers=headers, params=params)
        except Exception:
            return None

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    async def _fetch_paginated_rows(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any],
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        max_pages = max(1, int(getattr(self.settings, "bzzoiro_max_pages", 8) or 8))
        page = 1

        while page <= max_pages:
            request_params = {**params, "page": page}
            stats["requests"] += 1
            response = await self._safe_get(client, f"{self.base_url}{path}", headers=headers, params=request_params)
            if response is None:
                stats["response_errors"] += 1
                break
            stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:1500]
            if response.status_code != 200:
                stats["response_errors"] += 1
                break
            payload = self._safe_json(response)
            batch = self._results(payload)
            if not batch:
                break
            rows.extend(batch)
            next_url = payload.get("next") if isinstance(payload, dict) else None
            if not next_url:
                break
            page += 1

        return rows

    @staticmethod
    def _results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            data = payload.get("results")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def _match_event(self, match: Match, events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        for event in events:
            home = str(event.get("home_team") or "")
            away = str(event.get("away_team") or "")
            home_obj = event.get("home_team_obj") or {}
            away_obj = event.get("away_team_obj") or {}
            league = str((event.get("league") or {}).get("name") or "")
            try:
                start = parse_datetime(event.get("event_date"))
            except Exception:
                continue
            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home_candidates=[
                    home,
                    str(home_obj.get("name") or "").strip() if isinstance(home_obj, dict) else "",
                    str(home_obj.get("short_name") or "").strip() if isinstance(home_obj, dict) else "",
                ],
                event_away_candidates=[
                    away,
                    str(away_obj.get("name") or "").strip() if isinstance(away_obj, dict) else "",
                    str(away_obj.get("short_name") or "").strip() if isinstance(away_obj, dict) else "",
                ],
                event_start=start,
                event_league=league,
                exact_tolerance_hours=float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
                fuzzy_tolerance_hours=float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8),
            )
            if score > best_score:
                best = event
                best_score = score
                best_quality = quality
        if best_score < 48.0:
            return None, None
        return best, best_quality

    def _match_prediction(self, match: Match, predictions: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        for pred in predictions:
            event = pred.get("event") or {}
            home = str(event.get("home_team") or pred.get("home_team") or "")
            away = str(event.get("away_team") or pred.get("away_team") or "")
            home_obj = event.get("home_team_obj") or {}
            away_obj = event.get("away_team_obj") or {}
            if not home or not away:
                continue
            event_league = str((event.get("league") or {}).get("name") or pred.get("league") or "")
            event_date_raw = event.get("event_date") or pred.get("event_date") or pred.get("date")
            if event_date_raw not in (None, ""):
                try:
                    event_start = parse_datetime(event_date_raw)
                    exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
                    fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
                except Exception:
                    event_start = match.commence_time
                    exact_tol = 1.0
                    fuzzy_tol = 24.0
            else:
                event_start = match.commence_time
                exact_tol = 1.0
                fuzzy_tol = 24.0

            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home_candidates=[
                    home,
                    str(home_obj.get("name") or "").strip() if isinstance(home_obj, dict) else "",
                    str(home_obj.get("short_name") or "").strip() if isinstance(home_obj, dict) else "",
                ],
                event_away_candidates=[
                    away,
                    str(away_obj.get("name") or "").strip() if isinstance(away_obj, dict) else "",
                    str(away_obj.get("short_name") or "").strip() if isinstance(away_obj, dict) else "",
                ],
                event_start=event_start,
                event_league=event_league or match.league_name,
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if event_date_raw in (None, ""):
                score -= 4.0
            if not event_league:
                score -= 2.0
            if score > best_score:
                best = pred
                best_score = score
                best_quality = quality
        return (best, best_quality) if best_score >= 50.0 else (None, None)

    @staticmethod
    def _prediction_event(prediction: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        event = prediction.get("event") or {}
        if not isinstance(event, dict) or not events:
            return event if isinstance(event, dict) and event else None

        prediction_ids = {
            str(value).strip()
            for value in (event.get("id"), event.get("api_id"))
            if value not in (None, "")
        }
        if prediction_ids:
            for candidate in events:
                candidate_ids = {
                    str(value).strip()
                    for value in (candidate.get("id"), candidate.get("api_id"))
                    if value not in (None, "")
                }
                if prediction_ids & candidate_ids:
                    return candidate
        return event if event else None

    def _prediction_to_context(
        self,
        prediction: dict[str, Any],
        event: dict[str, Any] | None,
        match_quality: str | None,
    ) -> MatchContext:
        home_prob = normalize_probability_percent(prediction.get("prob_home_win"))
        away_prob = normalize_probability_percent(prediction.get("prob_away_win"))
        draw_prob = normalize_probability_percent(prediction.get("prob_draw"))
        over25 = normalize_probability_percent(prediction.get("prob_over_25"))
        btts_yes = normalize_probability_percent(prediction.get("prob_btts_yes"))

        expected_total = self._infer_total_lambda(over25) if over25 is not None else None
        expected_home = None
        expected_away = None
        if expected_total is not None:
            share = 0.5
            if home_prob is not None and away_prob is not None and (home_prob + away_prob) > 0:
                share = home_prob / (home_prob + away_prob)
            expected_home = round(clamp(expected_total * clamp(share, 0.28, 0.72), 0.25, 3.75), 3)
            expected_away = round(clamp(expected_total - expected_home, 0.25, 3.75), 3)

        raw_conf = prediction.get("confidence")
        raw_conf_pct: float | None = None
        confidence = 56.0
        try:
            raw_value = float(raw_conf)
            raw_conf_pct = raw_value * 100.0 if raw_value <= 1.0 else raw_value
            confidence = 56.0 + ((raw_conf_pct - 50.0) * 0.30)
            if raw_conf_pct < 50.0:
                confidence -= min(4.0, (50.0 - raw_conf_pct) * 0.12)
        except Exception:
            pass

        prediction_age_hours: float | None = None
        created_at_raw = prediction.get("created_at") or prediction.get("updated_at")
        if created_at_raw:
            try:
                created_at = parse_datetime(created_at_raw)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                prediction_age_hours = max(
                    0.0,
                    (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds() / 3600.0,
                )
                if prediction_age_hours > 72.0:
                    confidence -= min(8.0, ((prediction_age_hours - 72.0) / 24.0) * 1.5)
                elif prediction_age_hours > 36.0:
                    confidence -= min(3.0, (prediction_age_hours - 36.0) / 24.0)
            except Exception:
                prediction_age_hours = None

        if match_quality == "fuzzy":
            confidence -= 5.0
        elif match_quality == "loose":
            confidence -= 2.0
        elif match_quality is None:
            confidence -= 4.0

        if over25 is not None and btts_yes is not None and raw_conf_pct is not None and raw_conf_pct >= 55.0:
            confidence += 1.0
        confidence = clamp(confidence, 48.0, 74.0)

        return MatchContext(
            source="bzzoiro",
            payload={"prediction": prediction, "event": event},
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "bzzoiro_draw_probability": draw_prob,
                "bzzoiro_over25_probability": over25,
                "bzzoiro_btts_yes_probability": btts_yes,
                "bzzoiro_raw_confidence": round(raw_conf_pct, 3) if raw_conf_pct is not None else None,
                "bzzoiro_prediction_created_at": created_at_raw,
                "bzzoiro_prediction_age_hours": round(prediction_age_hours, 1) if prediction_age_hours is not None else None,
                "bzzoiro_match_quality": match_quality,
                "bzzoiro_model_version": prediction.get("model_version"),
            },
        )

    @staticmethod
    def _infer_total_lambda(prob_over_25: float | None) -> float | None:
        if prob_over_25 is None:
            return None
        target = clamp(float(prob_over_25), 0.08, 0.92)
        lo, hi = 0.5, 5.5
        for _ in range(30):
            mid = (lo + hi) / 2.0
            val = over_probability_from_lambda(mid, 2.5)
            if val is None:
                return None
            if val < target:
                lo = mid
            else:
                hi = mid
        return round((lo + hi) / 2.0, 3)
