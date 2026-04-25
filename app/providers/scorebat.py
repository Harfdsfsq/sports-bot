from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, parse_datetime, score_event_match

UTC = timezone.utc


class ScorebatContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = str(os.getenv("ENABLE_SCOREBAT_CONTEXT", "true")).strip().lower() in {"1", "true", "yes", "on"}
        self.base_url = os.getenv("SCOREBAT_BASE_URL", "https://www.scorebat.com/video-api/v3/")
        self.timeout = float(os.getenv("SCOREBAT_TIMEOUT_SECONDS") or 12.0)
        self.match_limit = max(1, int(os.getenv("SCOREBAT_CONTEXT_MATCH_LIMIT") or 36))
        self.cache_ttl_minutes = max(30, int(os.getenv("SCOREBAT_CACHE_TTL_MINUTES") or 180))
        self.score_threshold = float(os.getenv("SCOREBAT_MATCH_SCORE_THRESHOLD") or 45.0)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.enabled,
            "api_key_present": None,
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "cache_hit": False,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_contexts": []}
        if not self.enabled:
            return {}, stats, preview

        soccer_matches = [m for m in matches if str(getattr(m, "sport_key", "")) == "soccer"][: self.match_limit]
        if not soccer_matches:
            return {}, stats, preview

        payload = self._cache_get()
        if payload is not None:
            stats["cache_hit"] = True
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                stats["requests"] += 1
                try:
                    response = await client.get(self.base_url)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_error"] = f"{type(exc).__name__}: {exc}"
                    return {}, stats, preview
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    stats["http_status"] = response.status_code
                    stats["last_body_preview"] = response.text[:500]
                    return {}, stats, preview
                try:
                    payload = response.json()
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_error"] = f"json parse failed: {type(exc).__name__}: {exc}"
                    return {}, stats, preview
                self._cache_put(payload)

        rows = self._rows(payload)
        stats["events_fetched"] = len(rows)
        preview["sample_events"] = rows[:3]

        contexts: dict[str, MatchContext] = {}
        for match in soccer_matches:
            row, quality, score = self._match_row(match, rows)
            if row is None:
                continue
            context = self._row_to_context(match, row, quality, score)
            contexts[match.match_key] = context
            stats["contexts_built"] += 1
            if quality == "exact":
                stats["matched_exact"] += 1
            elif quality == "loose":
                stats["matched_loose"] += 1
            elif quality == "fuzzy":
                stats["matched_fuzzy"] += 1
            if len(preview["sample_contexts"]) < 4:
                preview["sample_contexts"].append({
                    "match_key": match.match_key,
                    "title": row.get("title"),
                    "competition": (row.get("competition") or {}).get("name") if isinstance(row.get("competition"), dict) else None,
                    "quality": quality,
                    "score": round(score, 2),
                })

        return contexts, stats, preview

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "scorebat_v3.json"

    def _cache_get(self) -> dict[str, Any] | None:
        path = self._cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched = parse_datetime(payload.get("fetched_at"))
        except Exception:
            return None
        if datetime.now(UTC) - fetched > timedelta(minutes=self.cache_ttl_minutes):
            return None
        data = payload.get("payload")
        return data if isinstance(data, dict) else None

    def _cache_put(self, payload: dict[str, Any]) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "payload": payload}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            value = payload.get("response") or payload.get("matches") or []
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def _match_row(self, match: Match, rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, float]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        for row in rows:
            title = str(row.get("title") or "")
            event_home, event_away = self._teams_from_title(title)
            if not event_home or not event_away:
                continue
            raw_date = row.get("date") or row.get("matchviewUrlDate")
            try:
                event_start = parse_datetime(str(raw_date))
            except Exception:
                event_start = match.commence_time
            competition = str((row.get("competition") or {}).get("name") or "") if isinstance(row.get("competition"), dict) else ""
            score, quality = score_event_match(
                sport="soccer",
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=event_home,
                event_away=event_away,
                event_start=event_start,
                event_league=competition,
                exact_tolerance_hours=float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
                fuzzy_tolerance_hours=float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8),
            )
            if score > best_score:
                best = row
                best_quality = quality
                best_score = score
        if best is None or best_score < self.score_threshold:
            return None, None, best_score
        return best, best_quality, best_score

    @staticmethod
    def _teams_from_title(title: str) -> tuple[str, str]:
        for sep in (" - ", " vs ", " v "):
            if sep in title:
                left, right = title.split(sep, 1)
                return left.strip(), right.strip()
        return "", ""

    def _row_to_context(self, match: Match, row: dict[str, Any], quality: str | None, score: float) -> MatchContext:
        videos = row.get("videos") if isinstance(row.get("videos"), list) else []
        competition = (row.get("competition") or {}).get("name") if isinstance(row.get("competition"), dict) else None
        confidence = 51.0
        if quality == "exact":
            confidence += 4.0
        elif quality == "loose":
            confidence += 2.5
        elif quality == "fuzzy":
            confidence += 1.0
        if videos:
            confidence += 0.8
        confidence = clamp(confidence, 50.0, 57.0)
        return MatchContext(
            source="scorebat",
            payload={"scorebat": row},
            expected_home=None,
            expected_away=None,
            confidence=confidence,
            details={
                "scorebat_title": row.get("title"),
                "scorebat_competition": competition,
                "scorebat_url": row.get("matchviewUrl") or row.get("url"),
                "scorebat_thumbnail": row.get("thumbnail"),
                "scorebat_video_count": len(videos),
                "scorebat_match_quality": quality,
                "scorebat_match_score": round(score, 3),
            },
        )
