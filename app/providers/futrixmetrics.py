from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, normalize_text


class FutrixMetricsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = getattr(settings, "futrixmetrics_api_key", None)
        self.base_url = str(getattr(settings, "futrixmetrics_base_url", "https://footballperformanceapi.site")).rstrip("/")
        self.timeout = float(getattr(settings, "futrixmetrics_timeout_seconds", 12.0) or 12.0)
        self.cache_ttl_hours = max(12, int(getattr(settings, "futrixmetrics_team_cache_ttl_hours", 168) or 168))
        self.limit_per_team = max(20, int(getattr(settings, "futrixmetrics_limit_per_team", 80) or 80))

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "team_cache_hits": 0,
            "contexts_built": 0,
            "http_statuses": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_rows": [], "sample_contexts": []}
        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        limit = max(1, int(getattr(self.settings, "futrixmetrics_context_match_limit", 6) or 6))
        target_matches = self._prioritize_matches(soccer_matches)[:limit]

        cache = self._load_cache()
        team_data: dict[str, list[dict[str, Any]]] = {}
        async with httpx.AsyncClient(timeout=self.timeout, headers={"X-API-Key": self.api_key}) as client:
            for match in target_matches:
                league = self._league_name(match.league_name)
                for team in [match.home_team, match.away_team]:
                    team_key = self._cache_team_key(team, league)
                    rows = self._cache_get(cache, team_key)
                    if rows is not None:
                        stats["team_cache_hits"] += 1
                        team_data[team_key] = rows
                        continue
                    rows = await self._fetch_team_rows(client, team, league, stats)
                    if rows is not None:
                        team_data[team_key] = rows
                        self._cache_put(cache, team_key, rows)
                        if rows and len(preview["sample_rows"]) < 2:
                            preview["sample_rows"].append(rows[0])

        self._write_cache(cache)

        contexts: dict[str, MatchContext] = {}
        for match in target_matches:
            league = self._league_name(match.league_name)
            home_rows = team_data.get(self._cache_team_key(match.home_team, league)) or []
            away_rows = team_data.get(self._cache_team_key(match.away_team, league)) or []
            context = self._build_context(match, home_rows, away_rows)
            if context is None:
                continue
            contexts[match.match_key] = context
            stats["contexts_built"] += 1
            if len(preview["sample_contexts"]) < 4:
                preview["sample_contexts"].append({
                    "match_key": match.match_key,
                    "confidence": context.confidence,
                    "home_form": context.details.get("home_form"),
                    "away_form": context.details.get("away_form"),
                })
        return contexts, stats, preview

    async def _fetch_team_rows(self, client: httpx.AsyncClient, team: str, league: str, stats: dict[str, Any]) -> list[dict[str, Any]] | None:
        params = {"team": team, "league": league, "limit": self.limit_per_team}
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}/database/ratings", params=params)
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_body_preview"] = f"request failed: {exc}"
            return None
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1600]
        if response.status_code != 200:
            stats["response_errors"] += 1
            return None
        try:
            payload = response.json()
        except Exception:
            stats["response_errors"] += 1
            return None
        return self._extract_rows(payload)

    def _build_context(self, match: Match, home_rows: list[dict[str, Any]], away_rows: list[dict[str, Any]]) -> MatchContext | None:
        home_avg = self._avg_rating(home_rows)
        away_avg = self._avg_rating(away_rows)
        if home_avg is None or away_avg is None:
            return None
        home_form = self._normalize_rating(home_avg)
        away_form = self._normalize_rating(away_avg)
        if home_form is None or away_form is None:
            return None
        diff = home_form - away_form
        home_prob = clamp(0.50 + diff * 0.28, 0.18, 0.74)
        away_prob = clamp(1.0 - home_prob, 0.18, 0.74)
        confidence = clamp(52.0 + min(len(home_rows), 20) * 0.25 + min(len(away_rows), 20) * 0.25, 52.0, 63.0)
        return MatchContext(
            source="futrixmetrics",
            payload={"home_rows": home_rows[:20], "away_rows": away_rows[:20]},
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "home_form": home_form,
                "away_form": away_form,
                "home_attack": clamp(home_form * 0.98, 0.0, 1.0),
                "away_attack": clamp(away_form * 0.98, 0.0, 1.0),
                "futrix_home_team_rating": round(home_avg, 3),
                "futrix_away_team_rating": round(away_avg, 3),
                "futrix_home_sample": len(home_rows),
                "futrix_away_sample": len(away_rows),
                "futrix_confidence": round(confidence, 2),
            },
        )

    @staticmethod
    def _avg_rating(rows: list[dict[str, Any]]) -> float | None:
        values: list[float] = []
        candidates = (
            "rating",
            "player_rating",
            "overall_rating",
            "score",
            "predicted_score",
            "base_score",
            "fm_score",
        )
        for row in rows:
            for key in candidates:
                val = row.get(key)
                try:
                    if val not in (None, ""):
                        values.append(float(val))
                        break
                except Exception:
                    continue
        if not values:
            return None
        sample = values[: min(len(values), 18)]
        return sum(sample) / len(sample)

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []

        direct = payload.get("data")
        if isinstance(direct, list):
            return [row for row in direct if isinstance(row, dict)]

        # Futrix can return scores in nested payloads instead of `data`.
        for key in ("pro_scores", "base_scores"):
            block = payload.get(key)
            if not isinstance(block, dict):
                continue
            rows = block.get("rows")
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]

        return []

    @staticmethod
    def _normalize_rating(value: float) -> float | None:
        try:
            v = float(value)
        except Exception:
            return None
        if v <= 1.0:
            return clamp(v, 0.0, 1.0)
        if v <= 10.0:
            return clamp(v / 10.0, 0.0, 1.0)
        return clamp(v / 100.0, 0.0, 1.0)

    @staticmethod
    def _league_name(name: str) -> str:
        return str(name or "").strip()

    @staticmethod
    def _cache_team_key(team: str, league: str) -> str:
        return f"{normalize_text(team)}::{normalize_text(league)}"

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "futrixmetrics_context.json"

    def _load_cache(self) -> dict[str, Any]:
        path = self._cache_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"teams": {}}

    def _cache_get(self, cache: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
        entry = (cache.get("teams") or {}).get(key)
        if not isinstance(entry, dict):
            return None
        try:
            fetched_at = datetime.fromisoformat(str(entry.get("fetched_at")).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return None
        if datetime.now(UTC) - fetched_at > timedelta(hours=self.cache_ttl_hours):
            return None
        rows = entry.get("rows")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else None

    @staticmethod
    def _cache_put(cache: dict[str, Any], key: str, rows: list[dict[str, Any]]) -> None:
        cache.setdefault("teams", {})[key] = {"fetched_at": datetime.now(UTC).isoformat(), "rows": rows[:200]}

    def _write_cache(self, cache: dict[str, Any]) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        def key(match: Match) -> tuple[int, float, str]:
            tier_rank = 0 if getattr(match, "tier", "mid") == "top" else 1 if getattr(match, "tier", "mid") == "mid" else 2
            kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
            return (tier_rank, kickoff_distance, match.league_name.lower())
        return sorted(matches, key=key)
