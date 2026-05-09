from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, leagues_related, parse_datetime, score_event_match_variants

UTC = timezone.utc


class BzzoiroContextProvider:
    """Bzzoiro v2 context provider using the documented /api/predictions/ endpoint.

    The previous v2 event stats endpoint often returns null live xG before kickoff,
    while /api/predictions/ contains the useful pre-match model fields from the
    public OpenAPI spec: expected_home_goals, expected_away_goals, prob_home_win,
    prob_draw, prob_away_win, prob_over_25, prob_btts_yes and confidence.
    """

    VERSION = "bzzoiro-predictions-v2-clean-2026-05-09-budget-compatible"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
        base = (os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
        self.api_root_url = (os.getenv("BZZOIRO_API_ROOT_URL") or base.replace("/api/v2", "/api")).rstrip("/")
        self.timeout = float(os.getenv("BZZOIRO_TIMEOUT_SECONDS") or getattr(settings, "bzzoiro_timeout_seconds", 20.0) or 20.0)
        self.page_size = max(20, min(200, int(float(os.getenv("BZZOIRO_PREDICTIONS_PAGE_SIZE", "100") or 100))))
        self.max_pages = max(1, int(float(os.getenv("BZZOIRO_PREDICTIONS_MAX_PAGES", "6") or 6)))
        self.max_http_requests = max(
            0,
            int(float(
                os.getenv("BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN")
                or os.getenv("BZZOIRO_REQUEST_BUDGET_GRANTED")
                or os.getenv("BZZOIRO_PER_RUN_MAX")
                or self.max_pages
                or 1
            )),
        )
        self._requests_used = 0
        self.enforce_context_limit = str(os.getenv("BZZOIRO_ENFORCE_CONTEXT_LIMIT", "false")).lower() in {"1", "true", "yes", "on"}

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "provider_version": self.VERSION,
            "api_version": "v2_predictions",
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "request_limit_removed": False,
            "max_http_requests_per_run": self.max_http_requests,
            "budget_exhausted": False,
            "context_limit_enforced": self.enforce_context_limit,
            "api_root_url": self.api_root_url,
            "requests": 0,
            "response_errors": 0,
            "predictions_fetched": 0,
            "events_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_predictions": 0,
            "unmatched_matches": 0,
            "rejected_low_score": 0,
            "rejected_league_mismatch": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_url": None,
            "last_error": None,
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_predictions": [], "matched_examples": [], "unmatched_examples": []}
        if not self.api_key:
            return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview
        if self.enforce_context_limit:
            limit = max(0, int(float(os.getenv("BZZOIRO_CONTEXT_MATCH_LIMIT", "0") or 0)))
            if limit > 0:
                soccer_matches = self._prioritize_matches(soccer_matches)[:limit]

        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)
        headers = {"Authorization": f"Token {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            predictions = await self._fetch_predictions(client, headers, min_dt.date().isoformat(), max_dt.date().isoformat(), stats)
        stats["predictions_fetched"] = len(predictions)
        stats["events_fetched"] = len(predictions)
        preview["sample_predictions"] = predictions[:3]

        contexts: dict[str, MatchContext] = {}
        used_prediction_ids: set[str] = set()
        for match in soccer_matches:
            pred, quality, score, diag = self._match_prediction(match, predictions)
            if pred is None or quality is None:
                stats["unmatched_matches"] += 1
                if len(preview["unmatched_examples"]) < 8:
                    preview["unmatched_examples"].append({
                        "match_key": match.match_key,
                        "home": match.home_team,
                        "away": match.away_team,
                        "league": match.league_name,
                        "best_prediction": diag,
                    })
                continue
            ctx = self._prediction_to_context(pred, quality)
            if ctx is None:
                stats["unmatched_matches"] += 1
                continue
            contexts[match.match_key] = ctx
            used_prediction_ids.add(str(pred.get("id") or self._event_id(pred) or ""))
            if quality == "exact":
                stats["matched_exact"] += 1
            elif quality == "loose":
                stats["matched_loose"] += 1
            elif quality == "fuzzy":
                stats["matched_fuzzy"] += 1
            if len(preview["matched_examples"]) < 10:
                ph, pa = self._prediction_teams(pred)
                preview["matched_examples"].append({
                    "match_key": match.match_key,
                    "match_home": match.home_team,
                    "match_away": match.away_team,
                    "prediction_id": pred.get("id"),
                    "prediction_home": ph,
                    "prediction_away": pa,
                    "quality": quality,
                    "score": round(score, 2),
                    "expected_home": ctx.expected_home,
                    "expected_away": ctx.expected_away,
                    "confidence": ctx.confidence,
                })
        stats["contexts_built"] = len(contexts)
        stats["unmatched_predictions"] = max(0, len(predictions) - len(used_prediction_ids))
        return contexts, stats, preview

    async def _fetch_predictions(self, client: httpx.AsyncClient, headers: dict[str, str], date_from: str, date_to: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            if self.max_http_requests <= 0 or self._requests_used >= self.max_http_requests:
                stats["budget_exhausted"] = True
                break
            payload = await self._get_json(client, "/predictions/", headers, {
                "date_from": date_from,
                "date_to": date_to,
                "upcoming": "true",
                "page": page,
            }, stats)
            batch = self._rows(payload)
            if not batch:
                break
            for row in batch:
                key = str(row.get("id") or self._event_id(row) or len(rows))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            # DRF pagination: no next means done. Plain list: one page is enough.
            if isinstance(payload, dict) and not payload.get("next"):
                break
            if isinstance(payload, list):
                break
        return rows

    async def _get_json(self, client: httpx.AsyncClient, path: str, headers: dict[str, str], params: dict[str, Any], stats: dict[str, Any]) -> Any | None:
        url = f"{self.api_root_url}{path}"
        stats["requests"] += 1
        self._requests_used += 1
        stats["last_url"] = url
        try:
            response = await client.get(url, headers=headers, params=params)
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"{exc.__class__.__name__}: {exc}"
            return None
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1600]
        if response.status_code < 200 or response.status_code >= 300:
            stats["response_errors"] += 1
            stats["last_error"] = f"http_status={response.status_code}"
            return None
        try:
            payload = response.json()
            stats["payload_shapes"].append(self._shape(payload))
            return payload
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"json_error:{exc.__class__.__name__}"
            return None

    def _match_prediction(self, match: Match, predictions: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, float, dict[str, Any] | None]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        best_diag: dict[str, Any] | None = None
        for pred in predictions:
            home, away = self._prediction_teams(pred)
            start = self._prediction_start(pred)
            league = self._prediction_league(pred)
            if not home or not away or start is None:
                continue
            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home_candidates=[home],
                event_away_candidates=[away],
                event_start=start,
                event_league=league,
                exact_tolerance_hours=8.0,
                fuzzy_tolerance_hours=26.0,
            )
            if score > best_score:
                best = pred
                best_quality = quality
                best_score = score
                best_diag = {"score": round(score, 2), "quality": quality, "home": home, "away": away, "league": league, "start": start.isoformat()}
        if best is None or best_quality is None:
            return None, None, 0.0, best_diag
        event_league = self._prediction_league(best)
        if best_quality == "fuzzy" and event_league and not leagues_related(match.league_name, event_league):
            if best_diag:
                best_diag["accepted"] = False
                best_diag["rejection_reason"] = "league_mismatch"
            return None, None, 0.0, best_diag
        min_score = 68.0 if best_quality == "fuzzy" else 58.0
        if event_league == "":
            min_score += 3.0
        if best_score < min_score:
            if best_diag:
                best_diag["accepted"] = False
                best_diag["rejection_reason"] = "low_score"
                best_diag["required_score"] = min_score
            return None, None, 0.0, best_diag
        return best, best_quality, best_score, best_diag

    def _prediction_to_context(self, pred: dict[str, Any], quality: str | None) -> MatchContext | None:
        expected_home = self._num(pred, "expected_home_goals", "expected_home", "home_xg", "homeXg")
        expected_away = self._num(pred, "expected_away_goals", "expected_away", "away_xg", "awayXg")
        home_prob = self._prob(pred, "prob_home_win", "home_win_probability", "homeWinProbability")
        away_prob = self._prob(pred, "prob_away_win", "away_win_probability", "awayWinProbability")
        draw_prob = self._prob(pred, "prob_draw", "draw_probability")
        over25_prob = self._prob(pred, "prob_over_25", "over_25_probability", "probOver25")
        btts_prob = self._prob(pred, "prob_btts_yes", "btts_probability", "probBttsYes")
        if expected_home is None and expected_away is None and home_prob is None and away_prob is None and over25_prob is None:
            return None
        if expected_home is not None:
            expected_home = round(clamp(expected_home, 0.15, 4.5), 3)
        if expected_away is not None:
            expected_away = round(clamp(expected_away, 0.15, 4.5), 3)
        confidence_raw = self._num(pred, "confidence")
        if confidence_raw is None:
            confidence = 59.0
        elif confidence_raw <= 1.0:
            confidence = 54.0 + confidence_raw * 18.0
        else:
            confidence = confidence_raw
        if expected_home is not None and expected_away is not None:
            confidence += 3.0
        if home_prob is not None and away_prob is not None:
            confidence += 2.0
        if over25_prob is not None:
            confidence += 1.0
        if quality == "fuzzy":
            confidence -= 5.0
        elif quality == "loose":
            confidence -= 2.0
        confidence = clamp(confidence, 54.0, 72.0)
        return MatchContext(
            source="bzzoiro_predictions_v2",
            payload=pred,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "bzzoiro_api_version": "v2_predictions",
                "bzzoiro_prediction_id": pred.get("id"),
                "bzzoiro_event_id": self._event_id(pred),
                "bzzoiro_match_quality": quality,
                "bzzoiro_draw_probability": draw_prob,
                "bzzoiro_over25_probability": over25_prob,
                "bzzoiro_btts_probability": btts_prob,
                "bzzoiro_model_version": pred.get("model_version"),
                "bzzoiro_most_likely_score": pred.get("most_likely_score"),
                "bzzoiro_predicted_result": pred.get("predicted_result"),
                "bzzoiro_provider_mode": "predictions_endpoint",
            },
        )

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "predictions", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    @staticmethod
    def _shape(payload: Any) -> str:
        if isinstance(payload, list):
            return f"list:{len(payload)}"
        if isinstance(payload, dict):
            return "dict:" + ",".join(sorted(str(k) for k in list(payload)[:8]))
        return type(payload).__name__

    @staticmethod
    def _event_obj(pred: dict[str, Any]) -> dict[str, Any]:
        event = pred.get("event")
        return event if isinstance(event, dict) else {}

    @classmethod
    def _event_id(cls, pred: dict[str, Any]) -> str:
        event = cls._event_obj(pred)
        return str(event.get("id") or pred.get("event_id") or pred.get("event") or "").strip()

    @classmethod
    def _prediction_teams(cls, pred: dict[str, Any]) -> tuple[str, str]:
        event = cls._event_obj(pred)
        def team(side: str) -> str:
            for key in (f"{side}_team", f"{side}Team", side, "home" if side == "home" else "away"):
                value = event.get(key, pred.get(key))
                if isinstance(value, dict):
                    value = value.get("name") or value.get("Name") or value.get("short_name") or value.get("shortName")
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        return team("home"), team("away")

    @classmethod
    def _prediction_start(cls, pred: dict[str, Any]) -> datetime | None:
        event = cls._event_obj(pred)
        for key in ("event_date", "date", "start", "startTime", "commence_time", "kickoff"):
            value = event.get(key, pred.get(key))
            if value in (None, ""):
                continue
            try:
                dt = parse_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except Exception:
                continue
        return None

    @classmethod
    def _prediction_league(cls, pred: dict[str, Any]) -> str:
        event = cls._event_obj(pred)
        league = event.get("league") or pred.get("league")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or league.get("league_name") or league.get("displayName") or "")
        return str(event.get("league_name") or event.get("leagueName") or pred.get("league_name") or pred.get("leagueName") or league or "")

    @staticmethod
    def _num(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            try:
                if value not in (None, ""):
                    return float(value)
            except Exception:
                continue
        return None

    @classmethod
    def _prob(cls, row: dict[str, Any], *keys: str) -> float | None:
        value = cls._num(row, *keys)
        if value is None:
            return None
        if value > 1.0:
            value /= 100.0
        return clamp(value, 0.01, 0.99)

    @staticmethod
    def _prioritize_matches(matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        return sorted(matches, key=lambda m: (0 if getattr(m, "tier", "mid") == "top" else 1, abs((m.commence_time - now).total_seconds()), m.league_name.lower()))
