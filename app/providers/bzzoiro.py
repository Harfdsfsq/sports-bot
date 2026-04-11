from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import (
    clamp,
    leagues_related,
    normalize_probability_percent,
    over_probability_from_lambda,
    parse_datetime,
    score_event_match_variants,
)


class BzzoiroContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://sports.bzzoiro.com/api"
        self.api_key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
        self.timeout = float(getattr(settings, "bzzoiro_timeout_seconds", 20.0) or 20.0)

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
            "event_matches": 0,
            "prediction_links": 0,
            "fallback_prediction_matches": 0,
            "unmatched_predictions": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {
            "sample_events": [],
            "sample_predictions": [],
            "matched_examples": [],
        }

        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        target_limit = max(1, int(getattr(self.settings, "bzzoiro_context_match_limit", len(soccer_matches)) or len(soccer_matches)))
        soccer_matches = self._prioritize_matches(soccer_matches)[:target_limit]

        headers = {"Authorization": f"Token {self.api_key}"}
        min_dt = min(match.commence_time for match in soccer_matches).astimezone(UTC)
        max_dt = max(match.commence_time for match in soccer_matches).astimezone(UTC)
        date_from = min_dt.date().isoformat()
        date_to = max_dt.date().isoformat()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
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
        used_prediction_ids: set[str] = set()

        for match in soccer_matches:
            event, event_quality, event_score = self._match_event(match, events)
            prediction: dict[str, Any] | None = None
            quality = event_quality
            linked_from_event = False

            if event is not None:
                stats["event_matches"] = int(stats.get("event_matches", 0) or 0) + 1
                prediction = self._prediction_for_event(event, predictions, used_prediction_ids)
                if prediction is not None:
                    stats["prediction_links"] = int(stats.get("prediction_links", 0) or 0) + 1
                    linked_from_event = True

            if prediction is None:
                prediction, quality, _ = self._match_prediction(match, predictions, used_prediction_ids)
                if prediction is not None:
                    stats["fallback_prediction_matches"] = int(stats.get("fallback_prediction_matches", 0) or 0) + 1
                    event = self._prediction_event(prediction, events) or event

            if prediction is None:
                stats["unmatched_predictions"] = int(stats.get("unmatched_predictions", 0) or 0) + 1
                continue

            prediction_id = self._prediction_identity(prediction)
            if prediction_id:
                used_prediction_ids.add(prediction_id)

            context = self._prediction_to_context(prediction, event, quality)
            contexts[match.match_key] = context
            stats["contexts_built"] = len(contexts)
            if quality == "exact":
                stats["matched_exact"] = int(stats.get("matched_exact", 0) or 0) + 1
            elif quality == "loose":
                stats["matched_loose"] = int(stats.get("matched_loose", 0) or 0) + 1
            elif quality == "fuzzy":
                stats["matched_fuzzy"] = int(stats.get("matched_fuzzy", 0) or 0) + 1

            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append(
                    {
                        "match_key": match.match_key,
                        "match_home": match.home_team,
                        "match_away": match.away_team,
                        "quality": quality,
                        "event_score": round(event_score, 2) if event is not None else None,
                        "linked_from_event": linked_from_event,
                        "prediction_id": prediction.get("id"),
                        "event_id": (event or {}).get("id"),
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                    }
                )

        return contexts, stats, preview

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)

        def key(match: Match) -> tuple[int, float, str]:
            tier_rank = 0 if getattr(match, "tier", "mid") == "top" else 1 if getattr(match, "tier", "mid") == "mid" else 2
            kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
            return (tier_rank, kickoff_distance, match.league_name.lower())

        return sorted(matches, key=key)

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
            stats["requests"] = int(stats.get("requests", 0) or 0) + 1
            response = await self._safe_get(client, f"{self.base_url}{path}", headers=headers, params=request_params)
            if response is None:
                stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
                break
            stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:1500]
            if response.status_code != 200:
                stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
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

    def _match_event(self, match: Match, events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, float]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        exact_tol, fuzzy_tol = self._matching_tolerances()

        for event in events:
            home_candidates, away_candidates = self._event_team_candidates(event)
            if not home_candidates or not away_candidates:
                continue
            try:
                start = parse_datetime(event.get("event_date"))
            except Exception:
                continue
            league = self._event_league_name(event)
            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home_candidates=home_candidates,
                event_away_candidates=away_candidates,
                event_start=start,
                event_league=league,
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if score > best_score:
                best = event
                best_score = score
                best_quality = quality

        if best is None or best_quality is None:
            return None, None, 0.0
        if not self._acceptable_match(match, self._event_league_name(best), best_quality, best_score):
            return None, None, 0.0
        return best, best_quality, best_score

    def _match_prediction(
        self,
        match: Match,
        predictions: list[dict[str, Any]],
        used_prediction_ids: set[str],
    ) -> tuple[dict[str, Any] | None, str | None, float]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        default_exact_tol, default_fuzzy_tol = self._matching_tolerances()

        for pred in predictions:
            prediction_id = self._prediction_identity(pred)
            if prediction_id and prediction_id in used_prediction_ids:
                continue

            event = pred.get("event") or {}
            home_candidates, away_candidates = self._event_team_candidates(event, pred)
            if not home_candidates or not away_candidates:
                continue

            event_league = self._event_league_name(event, pred)
            event_date_raw = None
            if isinstance(event, dict):
                event_date_raw = event.get("event_date")
            if event_date_raw in (None, ""):
                event_date_raw = pred.get("event_date") or pred.get("date")

            if event_date_raw not in (None, ""):
                try:
                    event_start = parse_datetime(event_date_raw)
                    exact_tol = default_exact_tol
                    fuzzy_tol = max(default_fuzzy_tol, 24.0)
                except Exception:
                    event_start = match.commence_time
                    exact_tol = 1.0
                    fuzzy_tol = 36.0
            else:
                event_start = match.commence_time
                exact_tol = 1.0
                fuzzy_tol = 36.0

            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home_candidates=home_candidates,
                event_away_candidates=away_candidates,
                event_start=event_start,
                event_league=event_league,
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if event_date_raw in (None, ""):
                score -= 4.0
            if not event_league:
                score -= 2.0
            if not self._acceptable_match(match, event_league, quality, score):
                continue
            if score > best_score:
                best = pred
                best_score = score
                best_quality = quality

        return best, best_quality, best_score

    def _acceptable_match(self, match: Match, event_league: str, quality: str | None, score: float) -> bool:
        if quality is None:
            return False
        min_score = 69.0 if quality == "fuzzy" else 64.0
        if quality == "fuzzy" and event_league and not leagues_related(match.league_name, event_league):
            return False
        if not event_league:
            min_score += 4.0
        return score >= min_score

    def _prediction_for_event(
        self,
        event: dict[str, Any],
        predictions: list[dict[str, Any]],
        used_prediction_ids: set[str],
    ) -> dict[str, Any] | None:
        event_ids = self._entity_ids(event)
        best: dict[str, Any] | None = None
        best_rank = -1.0

        for prediction in predictions:
            prediction_id = self._prediction_identity(prediction)
            if prediction_id and prediction_id in used_prediction_ids:
                continue
            prediction_event = prediction.get("event") or {}
            if not isinstance(prediction_event, dict):
                continue

            candidate_ids = self._entity_ids(prediction_event)
            if event_ids and candidate_ids and event_ids & candidate_ids:
                rank = self._prediction_confidence_score(prediction)
                if rank > best_rank:
                    best = prediction
                    best_rank = rank
                continue

            try:
                event_start = parse_datetime(event.get("event_date"))
                prediction_start = parse_datetime(prediction_event.get("event_date") or prediction.get("event_date") or prediction.get("date"))
            except Exception:
                continue

            event_home_candidates, event_away_candidates = self._event_team_candidates(event)
            prediction_home_candidates, prediction_away_candidates = self._event_team_candidates(prediction_event, prediction)
            if not event_home_candidates or not event_away_candidates or not prediction_home_candidates or not prediction_away_candidates:
                continue

            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=event_home_candidates[0],
                match_away=event_away_candidates[0],
                match_start=event_start,
                match_league=self._event_league_name(event),
                event_home_candidates=prediction_home_candidates,
                event_away_candidates=prediction_away_candidates,
                event_start=prediction_start,
                event_league=self._event_league_name(prediction_event, prediction),
                exact_tolerance_hours=6.0,
                fuzzy_tolerance_hours=24.0,
            )
            if quality is None or score < 82.0:
                continue
            rank = self._prediction_confidence_score(prediction) + (score / 1000.0)
            if rank > best_rank:
                best = prediction
                best_rank = rank

        return best

    @staticmethod
    def _prediction_event(prediction: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        event = prediction.get("event") or {}
        if not isinstance(event, dict) or not events:
            return event if isinstance(event, dict) and event else None

        prediction_ids = BzzoiroContextProvider._entity_ids(event)
        if prediction_ids:
            for candidate in events:
                if prediction_ids & BzzoiroContextProvider._entity_ids(candidate):
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
        over15 = normalize_probability_percent(prediction.get("prob_over_15"))
        over25 = normalize_probability_percent(prediction.get("prob_over_25"))
        over35 = normalize_probability_percent(prediction.get("prob_over_35"))
        btts_yes = normalize_probability_percent(prediction.get("prob_btts_yes"))
        favorite_prob = normalize_probability_percent(prediction.get("favorite_prob"))

        expected_home = self._to_float(prediction.get("expected_home_goals"))
        expected_away = self._to_float(prediction.get("expected_away_goals"))
        expected_total = None
        if expected_home is not None or expected_away is not None:
            expected_total = (expected_home or 0.0) + (expected_away or 0.0)
        elif over25 is not None:
            expected_total = self._infer_total_lambda(over25)

        if expected_total is not None:
            share = 0.5
            if home_prob is not None and away_prob is not None and (home_prob + away_prob) > 0:
                share = home_prob / (home_prob + away_prob)
            share = clamp(share, 0.28, 0.72)
            if expected_home is None and expected_away is None:
                expected_home = expected_total * share
                expected_away = expected_total - expected_home
            elif expected_home is None and expected_away is not None:
                expected_home = expected_total - expected_away
            elif expected_away is None and expected_home is not None:
                expected_away = expected_total - expected_home

        if expected_home is not None:
            expected_home = round(clamp(expected_home, 0.25, 3.75), 3)
        if expected_away is not None:
            expected_away = round(clamp(expected_away, 0.25, 3.75), 3)

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
                "bzzoiro_over15_probability": over15,
                "bzzoiro_over25_probability": over25,
                "bzzoiro_over35_probability": over35,
                "bzzoiro_btts_yes_probability": btts_yes,
                "bzzoiro_favorite": prediction.get("favorite"),
                "bzzoiro_favorite_probability": favorite_prob,
                "bzzoiro_most_likely_score": prediction.get("most_likely_score"),
                "bzzoiro_raw_confidence": round(raw_conf_pct, 3) if raw_conf_pct is not None else None,
                "bzzoiro_prediction_created_at": created_at_raw,
                "bzzoiro_prediction_age_hours": round(prediction_age_hours, 1) if prediction_age_hours is not None else None,
                "bzzoiro_match_quality": match_quality,
                "bzzoiro_model_version": prediction.get("model_version"),
            },
        )

    def _matching_tolerances(self) -> tuple[float, float]:
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        return exact_tol, max(fuzzy_tol, 24.0)

    @staticmethod
    def _entity_ids(payload: dict[str, Any]) -> set[str]:
        if not isinstance(payload, dict):
            return set()
        return {
            str(value).strip()
            for value in (payload.get("id"), payload.get("api_id"))
            if value not in (None, "")
        }

    @classmethod
    def _prediction_identity(cls, prediction: dict[str, Any]) -> str:
        prediction_id = str(prediction.get("id") or "").strip()
        if prediction_id:
            return f"prediction:{prediction_id}"

        event = prediction.get("event") if isinstance(prediction.get("event"), dict) else {}
        if isinstance(event, dict):
            event_ids = sorted(cls._entity_ids(event))
            if event_ids:
                return "event:" + "|".join(event_ids)

        parts = [
            str((event or {}).get("home_team") or prediction.get("home_team") or "").strip().lower(),
            str((event or {}).get("away_team") or prediction.get("away_team") or "").strip().lower(),
            str((event or {}).get("event_date") or prediction.get("event_date") or prediction.get("date") or "").strip(),
        ]
        return "|".join(part for part in parts if part)

    @staticmethod
    def _prediction_confidence_score(prediction: dict[str, Any]) -> float:
        raw_conf = prediction.get("confidence")
        try:
            value = float(raw_conf)
        except Exception:
            return 0.0
        return value * 100.0 if value <= 1.0 else value

    @staticmethod
    def _event_league_name(event: Any, fallback: dict[str, Any] | None = None) -> str:
        if isinstance(event, dict):
            league = event.get("league")
            if isinstance(league, dict):
                name = league.get("name")
                if name:
                    return str(name).strip()
            name = event.get("league")
            if isinstance(name, str) and name.strip():
                return name.strip()
        if isinstance(fallback, dict):
            name = fallback.get("league")
            if name:
                return str(name).strip()
        return ""

    @staticmethod
    def _event_team_candidates(event: Any, fallback: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
        event_payload = event if isinstance(event, dict) else {}
        home_obj = event_payload.get("home_team_obj") or {}
        away_obj = event_payload.get("away_team_obj") or {}

        home_candidates = [
            str(event_payload.get("home_team") or (fallback or {}).get("home_team") or "").strip(),
            str(home_obj.get("name") or "").strip() if isinstance(home_obj, dict) else "",
            str(home_obj.get("short_name") or "").strip() if isinstance(home_obj, dict) else "",
        ]
        away_candidates = [
            str(event_payload.get("away_team") or (fallback or {}).get("away_team") or "").strip(),
            str(away_obj.get("name") or "").strip() if isinstance(away_obj, dict) else "",
            str(away_obj.get("short_name") or "").strip() if isinstance(away_obj, dict) else "",
        ]
        return (
            [candidate for candidate in home_candidates if candidate],
            [candidate for candidate in away_candidates if candidate],
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

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
