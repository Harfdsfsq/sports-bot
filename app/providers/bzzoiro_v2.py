from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext, Offer
from app.utils import (
    clamp,
    implied_probability,
    leagues_related,
    parse_datetime,
    score_event_match_variants,
    strip_vig_three_way,
    strip_vig_two_way,
)

UTC = timezone.utc
EXPORT = Path('.data/exports')


def _persist_json(name: str, payload: Any) -> None:
    try:
        EXPORT.mkdir(parents=True, exist_ok=True)
        (EXPORT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    except Exception:
        pass


class BzzoiroContextProvider:
    VERSION = "bzzoiro-v2-clean-2026-04-29-full-event-persist"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
        self.api_key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
        self.timeout = float(os.getenv("BZZOIRO_TIMEOUT_SECONDS") or getattr(settings, "bzzoiro_timeout_seconds", 20.0) or 20.0)
        self.retries = max(0, int(float(os.getenv("BZZOIRO_REQUEST_RETRIES", "2") or 2)))
        self.retry_backoff = max(0.0, float(os.getenv("BZZOIRO_RETRY_BACKOFF_SECONDS", "0.75") or 0.75))
        self.page_size = min(200, max(1, int(float(os.getenv("BZZOIRO_V2_PAGE_SIZE", "200") or 200))))
        self.max_events = max(0, int(float(os.getenv("BZZOIRO_V2_MAX_EVENTS", "0") or 0)))
        self.fetch_event_odds = self._env_bool("BZZOIRO_V2_FETCH_EVENT_ODDS", True)
        self.fetch_event_stats = self._env_bool("BZZOIRO_V2_FETCH_EVENT_STATS", True)
        self.fetch_event_metadata = self._env_bool("BZZOIRO_V2_FETCH_EVENT_METADATA", False)
        self.fetch_event_prediction = self._env_bool("BZZOIRO_V2_FETCH_EVENT_PREDICTION", True)
        self.enforce_context_limit = self._env_bool("BZZOIRO_ENFORCE_CONTEXT_LIMIT", False)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {"provider_version": self.VERSION, "api_version": "v2", "enabled": bool(self.api_key), "api_key_present": bool(self.api_key), "requests": 0, "response_errors": 0, "retry_attempts": 0, "events_fetched": 0, "event_matches": 0, "event_odds_fetched": 0, "event_stats_fetched": 0, "event_metadata_fetched": 0, "event_prediction_fetched": 0, "contexts_built": 0, "matched_exact": 0, "matched_loose": 0, "matched_fuzzy": 0, "http_statuses": [], "payload_shapes": [], "last_url": None, "last_error": None, "last_body_preview": None}
        preview: dict[str, Any] = {"sample_events": [], "matched_examples": [], "unmatched_examples": []}
        if not self.api_key:
            return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview
        soccer_matches = self._prioritize_matches(soccer_matches)
        if self.enforce_context_limit:
            limit = max(0, int(float(os.getenv("BZZOIRO_CONTEXT_MATCH_LIMIT", "0") or 0)))
            if limit > 0: soccer_matches = soccer_matches[:limit]
        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC); max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)
        headers = {"Authorization": f"Token {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            events = await self._fetch_events(client, headers, min_dt.date().isoformat(), max_dt.date().isoformat(), stats)
            stats["events_fetched"] = len(events); preview["sample_events"] = events[:3]
            self._persist_events(events, mode='context', date_from=min_dt.date().isoformat(), date_to=max_dt.date().isoformat())
            contexts: dict[str, MatchContext] = {}; detail_cache: dict[int, dict[str, Any]] = {}
            for match in soccer_matches:
                event, quality, score, diag = self._match_event(match, events)
                if not event:
                    if len(preview["unmatched_examples"]) < 8: preview["unmatched_examples"].append({"match_key": match.match_key, "home": match.home_team, "away": match.away_team, "best_event": diag})
                    continue
                event_id = self._to_int(event.get("id")); details: dict[str, Any] = {"event": event, "odds": None, "stats": None, "metadata": None, "prediction": None}
                if event_id is not None:
                    details = detail_cache.get(event_id) or details
                    if event_id not in detail_cache:
                        if self.fetch_event_odds:
                            details["odds"] = await self._get_json(client, f"/events/{event_id}/odds/", headers, {}, stats)
                            if details["odds"] is not None: stats["event_odds_fetched"] += 1
                        if self.fetch_event_stats:
                            details["stats"] = await self._get_json(client, f"/events/{event_id}/stats/", headers, {}, stats)
                            if details["stats"] is not None: stats["event_stats_fetched"] += 1
                        if self.fetch_event_metadata:
                            details["metadata"] = await self._get_json(client, f"/events/{event_id}/metadata/", headers, {}, stats)
                            if details["metadata"] is not None: stats["event_metadata_fetched"] += 1
                        if self.fetch_event_prediction:
                            details["prediction"] = await self._get_json(client, f"/events/{event_id}/prediction/", headers, {}, stats)
                            if details["prediction"] is not None: stats["event_prediction_fetched"] += 1
                        detail_cache[event_id] = details
                context = self._event_to_context(details, quality)
                if context is None: continue
                contexts[match.match_key] = context; stats["contexts_built"] = len(contexts); stats["event_matches"] += 1
                if quality == "exact": stats["matched_exact"] += 1
                elif quality == "loose": stats["matched_loose"] += 1
                elif quality == "fuzzy": stats["matched_fuzzy"] += 1
                if len(preview["matched_examples"]) < 10: preview["matched_examples"].append({"match_key": match.match_key, "match_home": match.home_team, "match_away": match.away_team, "event_id": event.get("id"), "event_home": event.get("home_team"), "event_away": event.get("away_team"), "event_score": round(score, 2), "quality": quality})
        return contexts, stats, preview

    def _persist_events(self, events: list[dict[str, Any]], *, mode: str, date_from: str, date_to: str) -> None:
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), "source": "bzzoiro", "provider_version": self.VERSION, "mode": mode, "date_from": date_from, "date_to": date_to, "events": events, "event_count": len(events), "preview_only": False, "diagnosis": "persisted_full_provider_events" if events else "provider_returned_no_events"}
        _persist_json('latest-bzzoiro-events-raw.json', payload); _persist_json('latest-bzzoiro-events.json', payload)

    async def _fetch_events(self, client: httpx.AsyncClient, headers: dict[str, str], date_from: str, date_to: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []; offset = 0
        while True:
            payload = await self._get_json(client, "/events/", headers, {"date_from": date_from, "date_to": date_to, "limit": self.page_size, "offset": offset}, stats)
            batch = self._rows(payload)
            if not batch: break
            rows.extend(batch)
            if self.max_events > 0 and len(rows) >= self.max_events: return rows[: self.max_events]
            if len(batch) < self.page_size: break
            offset += self.page_size
        return rows

    async def _get_json(self, client: httpx.AsyncClient, path: str, headers: dict[str, str], params: dict[str, Any], stats: dict[str, Any]) -> Any | None:
        url = f"{self.base_url}{path}"
        for attempt in range(self.retries + 1):
            if attempt > 0:
                stats["retry_attempts"] += 1
                if self.retry_backoff > 0: await asyncio.sleep(self.retry_backoff * attempt)
            stats["requests"] += 1; stats["last_url"] = url
            try: response = await client.get(url, headers=headers, params=params)
            except Exception as exc:
                stats["last_error"] = f"{exc.__class__.__name__}: {exc}"
                if attempt >= self.retries: stats["response_errors"] += 1; return None
                continue
            stats["http_statuses"].append(response.status_code); stats["last_body_preview"] = response.text[:1200]
            if response.status_code == 200:
                stats["last_error"] = None
                try:
                    payload = response.json(); stats["payload_shapes"].append(self._shape(payload)); return payload
                except Exception as exc:
                    stats["last_error"] = f"json_error:{exc.__class__.__name__}"; stats["response_errors"] += 1; return None
            stats["last_error"] = f"http_status={response.status_code}"
            if response.status_code not in {408,409,425,429} and response.status_code < 500: stats["response_errors"] += 1; return None
        stats["response_errors"] += 1; return None

    # Keep the existing implementation surface by importing utility methods from the previous class shape.
    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list): return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "events"):
                value = payload.get(key)
                if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
        return []

    @staticmethod
    def _shape(payload: Any) -> str:
        if isinstance(payload, list): return f"list:{len(payload)}"
        if isinstance(payload, dict): return "dict:" + ",".join(sorted(str(k) for k in list(payload)[:8]))
        return type(payload).__name__

    @staticmethod
    def _prioritize_matches(matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC); return sorted(matches, key=lambda m: (0 if getattr(m, "tier", "mid") == "top" else 1, abs((m.commence_time - now).total_seconds()), m.league_name.lower()))

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try: return None if value in (None, "") else float(value)
        except Exception: return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try: return None if value in (None, "") else int(float(value))
        except Exception: return None

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        return default if raw is None or str(raw).strip() == "" else str(raw).strip().lower() in {"1","true","yes","on"}

    # Minimal compatibility methods; runtime patches may replace/enhance these.
    def _match_event(self, match: Match, events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, float, dict[str, Any] | None]:
        best = None; best_score = 0.0; best_diag = None
        for event in events:
            home = str(event.get('home_team') or event.get('home') or '').lower(); away = str(event.get('away_team') or event.get('away') or '').lower()
            if not home or not away: continue
            score = (80.0 if match.home_team.lower() in home or home in match.home_team.lower() else 0.0) + (80.0 if match.away_team.lower() in away or away in match.away_team.lower() else 0.0)
            score /= 2.0
            if score > best_score: best, best_score, best_diag = event, score, {'home': home, 'away': away, 'score': score}
        return (best, 'loose' if best_score >= 60 else None, best_score, best_diag) if best_score >= 60 else (None, None, 0.0, best_diag)

    def _event_to_context(self, details: dict[str, Any], quality: str | None) -> MatchContext | None:
        event = details.get('event') if isinstance(details.get('event'), dict) else {}
        home_price = self._to_float(event.get('odds_home') or event.get('home_odds'))
        away_price = self._to_float(event.get('odds_away') or event.get('away_odds'))
        if not home_price and not away_price: return None
        return MatchContext(source='bzzoiro', payload=details, expected_home=None, expected_away=None, home_win_probability=implied_probability(home_price) if home_price else None, away_win_probability=implied_probability(away_price) if away_price else None, confidence=54.0, details={'bzzoiro_match_quality': quality, 'bzzoiro_event_id': event.get('id')})

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {"provider_version": self.VERSION, "api_version": "v2", "enabled": bool(self.api_key), "api_key_present": bool(self.api_key), "mode": "offers", "requests": 0, "response_errors": 0, "events_fetched": 0, "event_matches": 0, "offers_parsed": 0, "http_statuses": [], "payload_shapes": []}
        preview: dict[str, Any] = {"sample_events": [], "sample_offers": [], "unmatched_examples": []}
        if not self.api_key: return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == 'soccer']
        if not soccer_matches: return {}, stats, preview
        soccer_matches = self._prioritize_matches(soccer_matches)[:max(0, int(float(os.getenv('BZZOIRO_ODDS_MATCH_LIMIT', '80') or 80)))]
        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC); max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)
        headers = {"Authorization": f"Token {self.api_key}"}
        offers_by_match: dict[str, list[Offer]] = {}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            events = await self._fetch_events(client, headers, min_dt.date().isoformat(), max_dt.date().isoformat(), stats)
            stats['events_fetched'] = len(events); preview['sample_events'] = events[:3]
            self._persist_events(events, mode='offers', date_from=min_dt.date().isoformat(), date_to=max_dt.date().isoformat())
        return offers_by_match, stats, preview
