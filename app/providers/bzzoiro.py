from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, normalize_probability_percent, parse_datetime, score_event_match
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
        now = datetime.now(UTC)
        date_from = now.date().isoformat()
        date_to = (now + timedelta(days=max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4)))).date().isoformat()

        async with httpx.AsyncClient(timeout=25.0) as client:
            stats["requests"] += 1
            events_resp = await self._safe_get(client, f"{self.base_url}/events/", headers=headers, params={"date_from": date_from, "date_to": date_to})
            if events_resp is None:
                stats["response_errors"] += 1
                return {}, stats, preview
            stats["http_statuses"].append(events_resp.status_code)
            stats["last_body_preview"] = events_resp.text[:1500]
            if events_resp.status_code != 200:
                stats["response_errors"] += 1
                return {}, stats, preview
            events_payload = self._safe_json(events_resp)
            events = self._results(events_payload)
            stats["events_fetched"] = len(events)
            if events:
                preview["sample_events"] = events[:3]

            stats["requests"] += 1
            preds_resp = await self._safe_get(client, f"{self.base_url}/predictions/", headers=headers, params={"upcoming": "true"})
            if preds_resp is None:
                stats["response_errors"] += 1
                return {}, stats, preview
            stats["http_statuses"].append(preds_resp.status_code)
            stats["last_body_preview"] = preds_resp.text[:1500]
            if preds_resp.status_code != 200:
                stats["response_errors"] += 1
                return {}, stats, preview
            preds_payload = self._safe_json(preds_resp)
            predictions = self._results(preds_payload)
            stats["predictions_fetched"] = len(predictions)
            if predictions:
                preview["sample_predictions"] = predictions[:3]

        contexts: dict[str, MatchContext] = {}
        for match in soccer_matches:
            event, quality = self._match_event(match, events)
            prediction = self._match_prediction(match, predictions)
            if prediction is None:
                stats["unmatched_predictions"] += 1
                continue
            context = self._prediction_to_context(prediction, event)
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
            league = str((event.get("league") or {}).get("name") or "")
            try:
                start = parse_datetime(event.get("event_date"))
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

    def _match_prediction(self, match: Match, predictions: list[dict[str, Any]]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for pred in predictions:
            event = pred.get("event") or {}
            home = str(event.get("home_team") or pred.get("home_team") or "")
            away = str(event.get("away_team") or pred.get("away_team") or "")
            if not home or not away:
                continue
            # Predictions endpoint example doesn't expose date/league, so match on teams.
            score, _ = score_event_match(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=home,
                event_away=away,
                event_start=match.commence_time,
                event_league=match.league_name,
                exact_tolerance_hours=1.0,
                fuzzy_tolerance_hours=24.0,
            )
            if score > best_score:
                best = pred
                best_score = score
        return best if best_score >= 54.0 else None

    def _prediction_to_context(self, prediction: dict[str, Any], event: dict[str, Any] | None) -> MatchContext:
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
        confidence = 60.0
        try:
            raw_value = float(raw_conf)
            confidence = 56.0 + (raw_value * 0.22 if raw_value > 1.0 else raw_value * 22.0)
        except Exception:
            pass
        if over25 is not None:
            confidence += 2.0
        if btts_yes is not None:
            confidence += 1.0
        confidence = clamp(confidence, 56.0, 78.0)

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
