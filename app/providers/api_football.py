"""Quota-safe API-Football context provider.

The free API-Football plan is small enough that requests must be targeted. Fixture lists
are cached per date; the remaining per-run budget is spent on the provider's prediction
endpoint for matches selected by the strict coverage planner. Fixture identity alone is
never emitted as context.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, parse_datetime, score_event_match

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / ".data" / "cache" / "api_football"
EXPORT = ROOT / ".data" / "exports" / "latest-api-football-context.json"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_cached_at(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("fetched_at_utc")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def _probability(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return max(0.0, min(1.0, number / 100.0 if number > 1.0 else number))


def _app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


class ApiFootballContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = str(
            getattr(settings, "api_football_key", None)
            or os.getenv("API_FOOTBALL_KEY")
            or ""
        ).strip()
        self.base_url = str(
            getattr(settings, "api_football_base_url", None)
            or "https://v3.football.api-sports.io"
        ).rstrip("/")
        self.timeout = max(
            5.0, float(os.getenv("API_FOOTBALL_TIMEOUT_SECONDS") or 20.0)
        )
        self.per_run_max = max(
            0,
            int(
                getattr(settings, "api_football_per_run_max", 0)
                or os.getenv("API_FOOTBALL_PER_RUN_MAX")
                or 0
            ),
        )
        self.context_limit = max(
            0,
            int(
                getattr(settings, "api_football_context_match_limit", 0)
                or os.getenv("API_FOOTBALL_CONTEXT_MATCH_LIMIT")
                or 0
            ),
        )
        self.enabled = bool(getattr(settings, "api_football_enabled", False))

    def supports_match(self, match: Match) -> bool:
        return getattr(match, "sport_key", "") == "soccer"

    async def fetch_context(
        self, matches: list[Match]
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.enabled,
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "fixture_requests": 0,
            "prediction_requests": 0,
            "fixture_cache_hits": 0,
            "prediction_cache_hits": 0,
            "response_errors": 0,
            "http_statuses": [],
            "target_matches": 0,
            "fixture_rows": 0,
            "events_matched": 0,
            "contexts_built": 0,
            "budget_exhausted": False,
            "max_http_requests_per_run": self.per_run_max,
            "source": "api_football",
            "publication_contract_relaxed": False,
        }
        preview: dict[str, Any] = {
            "matched": [],
            "near_misses": [],
            "contexts": [],
        }
        soccer = [match for match in matches if self.supports_match(match)]
        if self.context_limit > 0:
            soccer = soccer[: self.context_limit]
        stats["target_matches"] = len(soccer)
        if (
            not self.enabled
            or not self.api_key
            or self.per_run_max <= 0
            or not soccer
        ):
            stats["budget_exhausted"] = self.per_run_max <= 0
            self._export(stats, preview)
            return {}, stats, preview

        dates = sorted(
            {
                match.commence_time.astimezone(UTC).date().isoformat()
                for match in soccer
            }
            | {
                match.commence_time.astimezone(_app_timezone()).date().isoformat()
                for match in soccer
            }
        )[:4]
        headers = {"x-apisports-key": self.api_key}
        fixture_rows: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for date_key in dates:
                fixture_rows.extend(
                    await self._fixture_rows(client, date_key, stats)
                )
                if int(stats["requests"]) >= self.per_run_max:
                    break
            stats["fixture_rows"] = len(fixture_rows)
            mapping = self._match_fixtures(soccer, fixture_rows, stats, preview)
            stats["events_matched"] = len(mapping)

            contexts: dict[str, MatchContext] = {}
            for match in soccer:
                mapped = mapping.get(match.match_key)
                if mapped is None:
                    continue
                fixture_id = str(mapped["fixture_id"])
                prediction = await self._prediction(
                    client, fixture_id, stats
                )
                if prediction is None:
                    if int(stats["requests"]) >= self.per_run_max:
                        stats["budget_exhausted"] = True
                        break
                    continue
                context = self._build_context(match, prediction, mapped)
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats["contexts_built"] += 1
                if len(preview["contexts"]) < 8:
                    preview["contexts"].append(
                        {
                            "match_key": match.match_key,
                            "fixture_id": fixture_id,
                            "expected_home": context.expected_home,
                            "expected_away": context.expected_away,
                            "home_win_probability": context.home_win_probability,
                            "away_win_probability": context.away_win_probability,
                            "confidence": context.confidence,
                        }
                    )
                if int(stats["requests"]) >= self.per_run_max:
                    stats["budget_exhausted"] = True
                    break

        self._export(stats, preview)
        return contexts, stats, preview

    async def _fixture_rows(
        self,
        client: httpx.AsyncClient,
        date_key: str,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        path = CACHE_DIR / "fixtures" / f"{date_key}.json"
        cached = _load(path)
        fetched = _parse_cached_at(cached)
        if (
            isinstance(cached, dict)
            and fetched is not None
            and datetime.now(UTC) - fetched <= timedelta(hours=4)
            and isinstance(cached.get("rows"), list)
        ):
            stats["fixture_cache_hits"] += 1
            return [row for row in cached["rows"] if isinstance(row, dict)]
        payload = await self._get_json(
            client,
            "/fixtures",
            {"date": date_key, "timezone": "UTC"},
            stats,
        )
        stats["fixture_requests"] += 1
        rows = self._response_rows(payload)
        if rows:
            _write(
                path,
                {
                    "date": date_key,
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                    "rows": rows,
                },
            )
        return rows

    async def _prediction(
        self,
        client: httpx.AsyncClient,
        fixture_id: str,
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        path = CACHE_DIR / "predictions" / f"{fixture_id}.json"
        cached = _load(path)
        fetched = _parse_cached_at(cached)
        if (
            isinstance(cached, dict)
            and fetched is not None
            and datetime.now(UTC) - fetched <= timedelta(hours=18)
            and isinstance(cached.get("prediction"), dict)
        ):
            stats["prediction_cache_hits"] += 1
            return cached["prediction"]
        payload = await self._get_json(
            client,
            "/predictions",
            {"fixture": fixture_id},
            stats,
        )
        stats["prediction_requests"] += 1
        rows = self._response_rows(payload)
        prediction = rows[0] if rows else None
        if isinstance(prediction, dict):
            _write(
                path,
                {
                    "fixture_id": fixture_id,
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                    "prediction": prediction,
                },
            )
            return prediction
        return None

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        stats: dict[str, Any],
    ) -> Any:
        if int(stats.get("requests") or 0) >= self.per_run_max:
            stats["budget_exhausted"] = True
            return None
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}{path}", params=params)
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"{type(exc).__name__}: {exc}"
            return None
        stats["http_statuses"].append(response.status_code)
        stats["rate_limit_remaining"] = response.headers.get(
            "x-ratelimit-requests-remaining"
        )
        if response.status_code >= 400:
            stats["response_errors"] += 1
            stats["last_body_preview"] = response.text[:500]
            return None
        try:
            return response.json()
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    def _response_rows(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        value = payload.get("response")
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    @staticmethod
    def _event(row: dict[str, Any]) -> dict[str, Any] | None:
        fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
        teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
        home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
        away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
        league = row.get("league") if isinstance(row.get("league"), dict) else {}
        fixture_id = fixture.get("id")
        try:
            start = parse_datetime(fixture.get("date"))
        except Exception:
            start = None
        if fixture_id in (None, "") or start is None:
            return None
        home_name = str(home.get("name") or "").strip()
        away_name = str(away.get("name") or "").strip()
        if not home_name or not away_name:
            return None
        return {
            "fixture_id": str(fixture_id),
            "home": home_name,
            "away": away_name,
            "start": start,
            "league": str(league.get("name") or ""),
            "raw": row,
        }

    def _match_fixtures(
        self,
        matches: list[Match],
        rows: list[dict[str, Any]],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        by_date: dict[str, list[Match]] = defaultdict(list)
        for match in matches:
            for offset in (-1, 0, 1):
                day = match.commence_time.astimezone(UTC).date() + timedelta(days=offset)
                by_date[day.isoformat()].append(match)
                local_day = (
                    match.commence_time.astimezone(_app_timezone()).date()
                    + timedelta(days=offset)
                )
                by_date[local_day.isoformat()].append(match)
        mapping: dict[str, dict[str, Any]] = {}
        for row in rows:
            event = self._event(row)
            if event is None:
                continue
            candidates = by_date.get(event["start"].date().isoformat(), [])
            home_tokens = set(canonicalize_team_name(event["home"]).split())
            away_tokens = set(canonicalize_team_name(event["away"]).split())
            shortlist = [
                match
                for match in candidates
                if home_tokens.intersection(
                    canonicalize_team_name(match.home_team).split()
                )
                or away_tokens.intersection(
                    canonicalize_team_name(match.away_team).split()
                )
            ]
            if not shortlist:
                shortlist = candidates[:48]
            best: tuple[Match, float, str] | None = None
            seen: set[str] = set()
            for match in shortlist[:64]:
                if match.match_key in seen:
                    continue
                seen.add(match.match_key)
                try:
                    score, quality = score_event_match(
                        sport="soccer",
                        match_home=match.home_team,
                        match_away=match.away_team,
                        match_start=match.commence_time,
                        match_league=match.league_name,
                        event_home=event["home"],
                        event_away=event["away"],
                        event_start=event["start"],
                        event_league=event["league"],
                        exact_tolerance_hours=8,
                        fuzzy_tolerance_hours=16,
                    )
                except Exception:
                    continue
                if best is None or float(score) > best[1]:
                    best = (match, float(score), str(quality or ""))
            if best is None or best[1] < 58.0:
                if len(preview["near_misses"]) < 12:
                    preview["near_misses"].append(
                        {
                            "fixture_id": event["fixture_id"],
                            "home": event["home"],
                            "away": event["away"],
                            "start": event["start"].isoformat(),
                            "best_score": best[1] if best else 0.0,
                        }
                    )
                continue
            current = mapping.get(best[0].match_key)
            if current is None or best[1] > float(current["score"]):
                mapping[best[0].match_key] = {
                    **event,
                    "score": best[1],
                    "quality": best[2],
                }
                if len(preview["matched"]) < 12:
                    preview["matched"].append(
                        {
                            "match_key": best[0].match_key,
                            "fixture_id": event["fixture_id"],
                            "score": round(best[1], 2),
                            "quality": best[2],
                        }
                    )
        return mapping

    @staticmethod
    def _build_context(
        match: Match,
        prediction: dict[str, Any],
        mapped: dict[str, Any],
    ) -> MatchContext | None:
        predictions = (
            prediction.get("predictions")
            if isinstance(prediction.get("predictions"), dict)
            else {}
        )
        percent = (
            predictions.get("percent")
            if isinstance(predictions.get("percent"), dict)
            else {}
        )
        goals = (
            predictions.get("goals")
            if isinstance(predictions.get("goals"), dict)
            else {}
        )
        expected_home = _number(goals.get("home"))
        expected_away = _number(goals.get("away"))
        home_probability = _probability(percent.get("home"))
        away_probability = _probability(percent.get("away"))
        comparison = (
            prediction.get("comparison")
            if isinstance(prediction.get("comparison"), dict)
            else {}
        )
        teams = (
            prediction.get("teams")
            if isinstance(prediction.get("teams"), dict)
            else {}
        )
        has_model_signal = any(
            value is not None
            for value in (
                expected_home,
                expected_away,
                home_probability,
                away_probability,
            )
        ) or bool(comparison) or bool(teams)
        if not has_model_signal:
            return None
        confidence = 62.0
        confidence += 6.0 if expected_home is not None and expected_away is not None else 0.0
        confidence += 5.0 if home_probability is not None and away_probability is not None else 0.0
        confidence += 3.0 if comparison else 0.0
        confidence = min(78.0, confidence)
        winner = (
            predictions.get("winner")
            if isinstance(predictions.get("winner"), dict)
            else {}
        )
        return MatchContext(
            source="api_football",
            payload={
                "provider": "api_football",
                "fixture_id": mapped.get("fixture_id"),
                "predictions": predictions,
                "comparison": comparison,
                "teams": teams,
                "league": prediction.get("league"),
            },
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_probability,
            away_win_probability=away_probability,
            confidence=confidence,
            details={
                "match_key": match.match_key,
                "fixture_id": mapped.get("fixture_id"),
                "identity_score": mapped.get("score"),
                "identity_quality": mapped.get("quality"),
                "winner": winner.get("name"),
                "winner_comment": winner.get("comment"),
                "advice": predictions.get("advice"),
                "under_over": predictions.get("under_over"),
                "hard_context": True,
                "fixture_only": False,
            },
        )

    @staticmethod
    def _export(stats: dict[str, Any], preview: dict[str, Any]) -> None:
        try:
            _write(
                EXPORT,
                {
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "stats": stats,
                    "preview": preview,
                    "publication_contract_relaxed": False,
                },
            )
        except Exception:
            pass


__all__ = ["ApiFootballContextProvider"]
