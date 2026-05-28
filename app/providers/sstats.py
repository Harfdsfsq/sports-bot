from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import (
    canonicalize_league_name,
    canonicalize_team_name,
    clamp,
    leagues_related,
    parse_datetime,
    score_event_match,
    score_event_match_variants,
    team_similarity,
)


class SStatsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = "https://api.sstats.net/Games/list"
        self.max_http_requests = max(
            0,
            int(
                os.getenv("SSTATS_MAX_HTTP_REQUESTS_PER_RUN")
                or os.getenv("SSTATS_REQUESTS_MAX_PER_RUN")
                or getattr(settings, "sstats_requests_max_per_run", 0)
                or 0
            ),
        )

    async def fetch_context(
        self,
        matches: list[Match],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_sstats_context),
            "api_key_present": bool(self.settings.sstats_api_key),
            "requests": 0,
            "response_errors": 0,
            "retry_attempts": 0,
            "days_requested": 0,
            "chunk_windows_requested": 0,
            "rows_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
            "team_form_contexts_built": 0,
            "bzzoiro_enabled": bool(getattr(self.settings, "bzzoiro_api_key", None)),
            "bzzoiro_requests": 0,
            "bzzoiro_response_errors": 0,
            "bzzoiro_events_fetched": 0,
            "bzzoiro_contexts_built": 0,
            "bzzoiro_matched_exact": 0,
            "bzzoiro_matched_loose": 0,
            "bzzoiro_matched_fuzzy": 0,
            "bzzoiro_unmatched_rows": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
            "last_error": None,
            "last_url": self.url,
            "max_http_requests_per_run": self.max_http_requests,
            "budget_exhausted": False,
        }
        preview: dict[str, Any] = {
            "sample_rows": [],
            "sample_contexts": [],
            "matched_examples": [],
            "unmatched_rows": [],
            "team_form_examples": [],
            "sample_bzzoiro": [],
        }

        if not self.settings.enable_sstats_context:
            return {}, stats, preview
        if not self.settings.sstats_api_key:
            return {}, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview

        now_utc = datetime.now(UTC)
        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)

        # SStats often returns recent finished matches rather than the exact future fixture.
        # For context building we therefore query a wider historical window and then build
        # per-team form based estimates as a fallback.
        lookback_days = max(21, int(getattr(self.settings, "sstats_lookback_days", 21) or 21))
        from_date = (min(now_utc, min_dt) - timedelta(days=lookback_days)).date().isoformat()
        to_date = max(now_utc, max_dt).date().isoformat()
        stats["days_requested"] = (parse_datetime(to_date).date() - parse_datetime(from_date).date()).days + 1

        async with httpx.AsyncClient(timeout=self.settings.sstats_timeout_seconds) as client:
            rows = await self._fetch_rows(client, from_date, to_date, stats)

        stats["rows_fetched"] = len(rows)
        if rows:
            preview["sample_rows"] = rows[:3]

        contexts: dict[str, MatchContext] = {}
        best_scores: dict[str, float] = {}
        direct_window_hours = max(
            int(getattr(self.settings, "match_start_tolerance_hours", 12) or 12),
            int(getattr(self.settings, "fallback_match_start_tolerance_hours", 48) or 48),
        ) + 6
        earliest_match_start = min(m.commence_time for m in soccer_matches).astimezone(UTC)
        latest_match_start = max(m.commence_time for m in soccer_matches).astimezone(UTC)

        # Pass 1: direct event matching, but keep it strict to avoid false positives.
        # When SStats returns only historical rows, skip the expensive O(rows * matches)
        # matcher entirely for rows that are far outside the target fixture window.
        for row in rows:
            event_home = self._extract_team_name(row, "home")
            event_away = self._extract_team_name(row, "away")
            if not event_home or not event_away:
                stats["unmatched_rows"] += 1
                continue

            event_league = self._extract_league_name(row)
            event_start = self._extract_start(row)
            if event_start is None:
                stats["unmatched_rows"] += 1
                continue

            if event_start < earliest_match_start - timedelta(hours=direct_window_hours) or event_start > latest_match_start + timedelta(hours=direct_window_hours):
                stats["unmatched_rows"] += 1
                if len(preview["unmatched_rows"]) < 10:
                    preview["unmatched_rows"].append(
                        {
                            "home": event_home,
                            "away": event_away,
                            "league": event_league,
                            "date": event_start.isoformat(),
                            "reason": "outside_direct_window",
                        }
                    )
                continue

            best_match: Match | None = None
            best_score = 0.0
            best_quality: str | None = None

            for match in soccer_matches:
                score, quality = score_event_match(
                    sport=match.sport_key,
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=event_home,
                    event_away=event_away,
                    event_start=event_start,
                    event_league=event_league,
                    exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                    fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
                )
                if score > best_score:
                    best_match = match
                    best_score = score
                    best_quality = quality

            if best_match is None:
                stats["unmatched_rows"] += 1
                if len(preview["unmatched_rows"]) < 10:
                    preview["unmatched_rows"].append(
                        {
                            "home": event_home,
                            "away": event_away,
                            "league": event_league,
                            "date": event_start.isoformat(),
                        }
                    )
                continue

            if best_quality == "fuzzy" and best_score < 74.0:
                # Avoid mapping arbitrary historical fixtures onto unrelated future matches.
                stats["unmatched_rows"] += 1
                continue

            context = self._row_to_context(row)
            if context.expected_home is None and context.expected_away is None:
                # Keep room for team-form fallback instead of storing an empty context.
                continue

            previous_score = best_scores.get(best_match.match_key)
            if previous_score is not None and previous_score >= best_score:
                continue

            contexts[best_match.match_key] = context
            best_scores[best_match.match_key] = best_score
            stats["contexts_built"] = len(contexts)

            if best_quality == "exact":
                stats["matched_exact"] += 1
            elif best_quality == "loose":
                stats["matched_loose"] += 1
            elif best_quality == "fuzzy":
                stats["matched_fuzzy"] += 1

            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append(
                    {
                        "match_key": best_match.match_key,
                        "match_home": best_match.home_team,
                        "match_away": best_match.away_team,
                        "row_home": event_home,
                        "row_away": event_away,
                        "league": event_league,
                        "quality": best_quality,
                        "score": round(best_score, 2),
                    }
                )
            if len(preview["sample_contexts"]) < 5:
                preview["sample_contexts"].append(
                    {
                        "match_key": best_match.match_key,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                        "source": context.source,
                    }
                )

        # Pass 2: fallback from recent team form for matches still lacking context.
        fallback_contexts = self._build_team_form_contexts(soccer_matches, rows, preview)
        added_fallback = 0
        for match_key, context in fallback_contexts.items():
            existing = contexts.get(match_key)
            if existing is not None and (existing.expected_home is not None or existing.expected_away is not None):
                continue
            contexts[match_key] = context
            added_fallback += 1
            if len(preview["sample_contexts"]) < 5:
                preview["sample_contexts"].append(
                    {
                        "match_key": match_key,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                        "source": context.source,
                    }
                )

        stats["team_form_contexts_built"] = added_fallback

        bzz_preview: list[dict[str, Any]] = []
        if getattr(self.settings, "bzzoiro_api_key", None) and bool(getattr(self.settings, "enable_bzzoiro_context", True)):
            timeout = float(getattr(self.settings, "bzzoiro_timeout_seconds", 20.0) or 20.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                bzz_contexts, bzz_stats, bzz_preview = await self._fetch_bzzoiro_contexts(client, soccer_matches)
            stats["bzzoiro_requests"] = int(bzz_stats.get("requests", 0) or 0)
            stats["bzzoiro_response_errors"] = int(bzz_stats.get("response_errors", 0) or 0)
            stats["bzzoiro_events_fetched"] = int(bzz_stats.get("events_fetched", 0) or 0)
            stats["bzzoiro_contexts_built"] = int(bzz_stats.get("contexts_built", 0) or 0)
            stats["bzzoiro_matched_exact"] = int(bzz_stats.get("matched_exact", 0) or 0)
            stats["bzzoiro_matched_loose"] = int(bzz_stats.get("matched_loose", 0) or 0)
            stats["bzzoiro_matched_fuzzy"] = int(bzz_stats.get("matched_fuzzy", 0) or 0)
            stats["bzzoiro_unmatched_rows"] = int(bzz_stats.get("unmatched_rows", 0) or 0)
            if bzz_preview:
                preview["sample_bzzoiro"] = bzz_preview[:10]
            for match_key, context in bzz_contexts.items():
                existing = contexts.get(match_key)
                if existing is None or str(existing.source or "") == "sstats_form":
                    contexts[match_key] = context

        stats["contexts_built"] = len(contexts)
        return contexts, stats, preview

    async def _fetch_rows(
        self,
        client: httpx.AsyncClient,
        from_date: str,
        to_date: str,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chunk_days = max(1, int(getattr(self.settings, "sstats_request_chunk_days", 7) or 7))
        rows: list[dict[str, Any]] = []
        seen_signatures: set[tuple[Any, ...]] = set()

        for window_from, window_to in self._date_windows(from_date, to_date, chunk_days):
            if self._http_budget_exhausted(stats):
                break
            stats["chunk_windows_requested"] = int(stats.get("chunk_windows_requested", 0) or 0) + 1
            batch_rows = await self._fetch_rows_window(client, window_from, window_to, stats)
            for row in batch_rows:
                signature = (
                    row.get("id"),
                    row.get("flashId"),
                    row.get("date"),
                    self._extract_team_name(row, "home"),
                    self._extract_team_name(row, "away"),
                )
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                rows.append(row)

        return rows

    async def _fetch_rows_window(
        self,
        client: httpx.AsyncClient,
        from_date: str,
        to_date: str,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        limit = 1000
        offset = 0
        total_count: int | None = None
        rows: list[dict[str, Any]] = []

        while True:
            if self._http_budget_exhausted(stats):
                break
            params = {
                "from": from_date,
                "to": to_date,
                "limit": limit,
                "offset": offset,
                "apikey": self.settings.sstats_api_key,
            }

            response = await self._request_with_retries(client, self.url, params=params, stats=stats)
            if response is None:
                break

            try:
                payload = response.json()
            except Exception as exc:
                stats["response_errors"] += 1
                stats["last_error"] = self._format_exception(exc)
                stats["last_body_preview"] = f"json parse failed: {stats['last_error']}"
                break

            if isinstance(payload, dict):
                shape = ",".join(sorted(payload.keys())[:12])
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)
                data = payload.get("data") or payload.get("results") or []
                try:
                    raw_total = payload.get("count")
                    if raw_total not in (None, ""):
                        total_count = int(raw_total)
                except Exception:
                    total_count = total_count
            elif isinstance(payload, list):
                if "list" not in stats["payload_shapes"]:
                    stats["payload_shapes"].append("list")
                data = payload
            else:
                shape = type(payload).__name__
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)
                break

            if not isinstance(data, list):
                break

            batch = [row for row in data if isinstance(row, dict)]
            if not batch:
                break

            rows.extend(batch)

            if len(batch) < limit:
                break

            offset += len(batch)
            # SStats frequently reports `count == batch_size` for the current page,
            # not for the whole date window. Trust explicit totals only when they
            # are clearly larger than one page.
            if total_count is not None and total_count > limit and offset >= total_count:
                break

        return rows

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any],
        stats: dict[str, Any],
        headers: dict[str, str] | None = None,
        preview_limit: int = 2000,
    ) -> httpx.Response | None:
        retries = max(0, int(getattr(self.settings, "sstats_request_retries", 2) or 2))
        backoff = max(0.0, float(getattr(self.settings, "sstats_retry_backoff_seconds", 1.0) or 1.0))

        for attempt in range(retries + 1):
            if self._http_budget_exhausted(stats):
                return None
            if attempt > 0:
                stats["retry_attempts"] = int(stats.get("retry_attempts", 0) or 0) + 1

            stats["requests"] = int(stats.get("requests", 0) or 0) + 1
            stats["last_url"] = url
            try:
                response = await client.get(url, params=params, headers=headers)
            except Exception as exc:
                error_text = self._format_exception(exc)
                stats["last_error"] = error_text
                stats["last_body_preview"] = f"request failed: {error_text}"
                if attempt >= retries:
                    stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
                    return None
                if backoff > 0:
                    await asyncio.sleep(backoff * attempt if attempt > 0 else backoff)
                continue

            http_statuses = stats.get("http_statuses")
            if isinstance(http_statuses, list):
                http_statuses.append(response.status_code)
            stats["last_body_preview"] = response.text[:preview_limit]
            if response.status_code == 200:
                stats["last_error"] = None
                return response

            stats["last_error"] = f"http_status={response.status_code}"
            if response.status_code == 429:
                stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
                stats["rate_limited"] = True
                stats["budget_exhausted"] = True
                stats["stop_on_429"] = True
                # Per-run stop only; do not write daily/monthly state.
                self.max_http_requests = min(self.max_http_requests, int(stats.get("requests") or 0)) if self.max_http_requests > 0 else self.max_http_requests
                return None
            if response.status_code >= 500:
                stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
                stats["server_error_stop"] = True
                stats["budget_exhausted"] = True
                self.max_http_requests = min(self.max_http_requests, int(stats.get("requests") or 0)) if self.max_http_requests > 0 else self.max_http_requests
                return None
            if attempt >= retries or not self._retryable_status(response.status_code):
                stats["response_errors"] = int(stats.get("response_errors", 0) or 0) + 1
                return None
            if backoff > 0:
                await asyncio.sleep(backoff * attempt if attempt > 0 else backoff)

        return None

    def _http_budget_exhausted(self, stats: dict[str, Any]) -> bool:
        if self.max_http_requests <= 0:
            return False
        if int(stats.get("requests", 0) or 0) >= self.max_http_requests:
            stats["budget_exhausted"] = True
            stats["last_error"] = "budget_exhausted"
            return True
        return False

    @staticmethod
    def _retryable_status(status_code: int) -> bool:
        return status_code in {408, 409, 425, 429} or 500 <= status_code < 600

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return f"{exc.__class__.__name__}: {message}"
        return exc.__class__.__name__

    @staticmethod
    def _date_windows(from_date: str, to_date: str, chunk_days: int) -> list[tuple[str, str]]:
        start = parse_datetime(from_date).date()
        end = parse_datetime(to_date).date()
        windows: list[tuple[str, str]] = []

        while start <= end:
            window_end = min(end, start + timedelta(days=chunk_days - 1))
            windows.append((start.isoformat(), window_end.isoformat()))
            start = window_end + timedelta(days=1)

        return windows

    async def _fetch_bzzoiro_contexts(
        self,
        client: httpx.AsyncClient,
        matches: list[Match],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], list[dict[str, Any]]]:
        stats: dict[str, Any] = {
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
        }
        preview: list[dict[str, Any]] = []
        api_key = getattr(self.settings, "bzzoiro_api_key", None)
        if not api_key or not matches:
            return {}, stats, preview

        min_dt = min(m.commence_time for m in matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in matches).astimezone(UTC)
        from_date = min_dt.date().isoformat()
        to_date = max_dt.date().isoformat()
        max_pages = max(1, int(getattr(self.settings, "bzzoiro_max_pages", 8) or 8))

        rows: list[dict[str, Any]] = []
        next_page: int | None = 1
        headers = {"Authorization": f"Token {api_key}"}
        while next_page is not None and next_page <= max_pages:
            params = {
                "date_from": from_date,
                "date_to": to_date,
                "upcoming": "true",
                "tz": "UTC",
                "page": next_page,
            }
            response = await self._request_with_retries(
                client,
                "https://sports.bzzoiro.com/api/predictions/",
                params=params,
                headers=headers,
                stats=stats,
                preview_limit=1500,
            )
            if response is None:
                break
            try:
                payload = response.json()
            except Exception:
                stats["response_errors"] += 1
                break

            batch = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(batch, list) or not batch:
                break
            rows.extend([item for item in batch if isinstance(item, dict)])
            next_url = payload.get("next") if isinstance(payload, dict) else None
            if next_url:
                next_page += 1
            else:
                next_page = None

        stats["events_fetched"] = len(rows)
        contexts: dict[str, MatchContext] = {}
        best_scores: dict[str, float] = {}
        for row in rows:
            event = row.get("event") or {}
            if not isinstance(event, dict):
                stats["unmatched_rows"] += 1
                continue
            event_home = str(event.get("home_team") or "").strip()
            event_away = str(event.get("away_team") or "").strip()
            home_obj = event.get("home_team_obj") or {}
            away_obj = event.get("away_team_obj") or {}
            home_candidates = [
                event_home,
                str(home_obj.get("name") or "").strip() if isinstance(home_obj, dict) else "",
                str(home_obj.get("short_name") or "").strip() if isinstance(home_obj, dict) else "",
            ]
            away_candidates = [
                event_away,
                str(away_obj.get("name") or "").strip() if isinstance(away_obj, dict) else "",
                str(away_obj.get("short_name") or "").strip() if isinstance(away_obj, dict) else "",
            ]
            event_league = str((event.get("league") or {}).get("name") or "").strip() if isinstance(event.get("league"), dict) else str(event.get("league") or "").strip()
            if not event_home or not event_away:
                stats["unmatched_rows"] += 1
                continue
            try:
                event_start = parse_datetime(event.get("event_date"))
            except Exception:
                stats["unmatched_rows"] += 1
                continue

            best_match: Match | None = None
            best_score = 0.0
            best_quality: str | None = None
            for match in matches:
                score, quality, matched_home, matched_away = score_event_match_variants(
                    sport=match.sport_key,
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home_candidates=home_candidates,
                    event_away_candidates=away_candidates,
                    event_start=event_start,
                    event_league=event_league,
                    exact_tolerance_hours=self.settings.match_start_tolerance_hours,
                    fuzzy_tolerance_hours=self.settings.fallback_match_start_tolerance_hours,
                )
                if score > best_score:
                    best_match = match
                    best_score = score
                    best_quality = quality
                    event_home = matched_home or event_home
                    event_away = matched_away or event_away
            if best_match is None:
                stats["unmatched_rows"] += 1
                continue
            league_match = canonicalize_league_name(best_match.league_name)
            league_event_norm = canonicalize_league_name(event_league)
            related_leagues = leagues_related(league_match, league_event_norm)
            min_score = 69.0 if best_quality == "fuzzy" else 64.0
            if best_quality == "fuzzy" and not related_leagues:
                stats["unmatched_rows"] += 1
                continue
            if best_score < min_score:
                stats["unmatched_rows"] += 1
                continue
            context = self._row_to_bzzoiro_context(row, best_quality)
            if context.expected_home is None or context.expected_away is None:
                stats["unmatched_rows"] += 1
                continue
            previous_score = best_scores.get(best_match.match_key)
            if previous_score is not None and previous_score >= best_score:
                continue
            contexts[best_match.match_key] = context
            best_scores[best_match.match_key] = best_score
            if best_quality == "exact":
                stats["matched_exact"] += 1
            elif best_quality == "loose":
                stats["matched_loose"] += 1
            elif best_quality == "fuzzy":
                stats["matched_fuzzy"] += 1
            if len(preview) < 10:
                preview.append({
                    "match_key": best_match.match_key,
                    "match_home": best_match.home_team,
                    "match_away": best_match.away_team,
                    "provider_home": event_home,
                    "provider_away": event_away,
                    "expected_home": context.expected_home,
                    "expected_away": context.expected_away,
                    "source": context.source,
                    "quality": best_quality,
                    "score": round(best_score, 2),
                    "leagues_related": related_leagues,
                })
        stats["contexts_built"] = len(contexts)
        return contexts, stats, preview

    def _row_to_bzzoiro_context(self, row: dict[str, Any], match_quality: str | None) -> MatchContext:
        def pct(value: Any) -> float | None:
            try:
                if value is None or value == "":
                    return None
                num = float(value)
                if num > 1.0:
                    num /= 100.0
                return clamp(num, 0.01, 0.95)
            except Exception:
                return None

        expected_home = self._to_float(row.get("expected_home_goals"))
        expected_away = self._to_float(row.get("expected_away_goals"))
        home_prob = pct(row.get("prob_home_win"))
        away_prob = pct(row.get("prob_away_win"))
        draw_prob = pct(row.get("prob_draw"))
        prob_over_15 = pct(row.get("prob_over_15"))
        prob_over_25 = pct(row.get("prob_over_25"))
        prob_over_35 = pct(row.get("prob_over_35"))
        prob_btts_yes = pct(row.get("prob_btts_yes"))
        favorite_prob = pct(row.get("favorite_prob"))

        raw_conf = row.get("confidence")
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
        created_at_raw = row.get("created_at") or row.get("updated_at")
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

        if prob_over_25 is not None and prob_btts_yes is not None and raw_conf_pct is not None and raw_conf_pct >= 55.0:
            confidence += 1.0
        confidence = clamp(confidence, 48.0, 74.0)

        return MatchContext(
            source="bzzoiro_predictions",
            payload=row,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "sstats_mode": "bzzoiro_prediction",
                "prob_draw": draw_prob,
                "prob_over_1_5": prob_over_15,
                "prob_over_2_5": prob_over_25,
                "prob_over_3_5": prob_over_35,
                "prob_btts_yes": prob_btts_yes,
                "favorite": row.get("favorite"),
                "favorite_prob": favorite_prob,
                "most_likely_score": row.get("most_likely_score"),
                "model_version": row.get("model_version"),
                "provider_confidence": raw_conf_pct / 100.0 if raw_conf_pct is not None else None,
                "provider_confidence_pct": round(raw_conf_pct, 3) if raw_conf_pct is not None else None,
                "prediction_created_at": created_at_raw,
                "prediction_age_hours": round(prediction_age_hours, 1) if prediction_age_hours is not None else None,
                "match_quality": match_quality,
            },
        )

    def _build_team_form_contexts(
        self,
        matches: list[Match],
        rows: list[dict[str, Any]],
        preview: dict[str, Any],
    ) -> dict[str, MatchContext]:
        team_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        canonical_keys: set[str] = set()

        for row in rows:
            start = self._extract_start(row)
            home = self._extract_team_name(row, "home")
            away = self._extract_team_name(row, "away")
            if start is None or not home or not away:
                continue

            home_goals = self._extract_result(row, "home")
            away_goals = self._extract_result(row, "away")
            if home_goals is None or away_goals is None:
                continue

            league = self._extract_league_name(row)
            home_key = canonicalize_team_name(home)
            away_key = canonicalize_team_name(away)
            if not home_key or not away_key:
                continue

            team_rows[home_key].append(
                {
                    "team": home,
                    "opponent": away,
                    "team_key": home_key,
                    "opponent_key": away_key,
                    "league": league,
                    "start": start,
                    "goals_for": float(home_goals),
                    "goals_against": float(away_goals),
                    "home": True,
                }
            )
            team_rows[away_key].append(
                {
                    "team": away,
                    "opponent": home,
                    "team_key": away_key,
                    "opponent_key": home_key,
                    "league": league,
                    "start": start,
                    "goals_for": float(away_goals),
                    "goals_against": float(home_goals),
                    "home": False,
                }
            )
            canonical_keys.add(home_key)
            canonical_keys.add(away_key)

        for rows_for_team in team_rows.values():
            rows_for_team.sort(key=lambda item: item["start"], reverse=True)

        resolved_cache: dict[str, str | None] = {}
        contexts: dict[str, MatchContext] = {}
        recent_limit = max(4, int(getattr(self.settings, "sstats_recent_matches", 6) or 6))
        min_sample = max(2, int(getattr(self.settings, "sstats_form_min_sample_per_team", 3) or 3))

        for match in matches:
            home_key = self._resolve_team_key(match.home_team, canonical_keys, resolved_cache)
            away_key = self._resolve_team_key(match.away_team, canonical_keys, resolved_cache)
            if not home_key or not away_key:
                continue

            home_recent = self._select_recent_rows(
                team_rows.get(home_key, []),
                league_name=match.league_name,
                before=match.commence_time,
                limit=recent_limit,
            )
            away_recent = self._select_recent_rows(
                team_rows.get(away_key, []),
                league_name=match.league_name,
                before=match.commence_time,
                limit=recent_limit,
            )
            if not home_recent or not away_recent:
                continue
            if min(len(home_recent), len(away_recent)) < min_sample:
                continue

            expected_home = self._blend_expected_goals(home_recent, away_recent, side="home")
            expected_away = self._blend_expected_goals(home_recent, away_recent, side="away")
            if expected_home is None or expected_away is None:
                continue

            total = expected_home + expected_away + 0.82
            home_prob = clamp(expected_home / total, 0.08, 0.82)
            away_prob = clamp(expected_away / total, 0.08, 0.82)
            league_overlap = int(self._count_league_matches(home_recent, match.league_name) + self._count_league_matches(away_recent, match.league_name))
            confidence = clamp(
                53.0
                + min(len(home_recent), 4) * 1.6
                + min(len(away_recent), 4) * 1.6
                + min(league_overlap, 4) * 0.8,
                54.0,
                65.0,
            )
            home_goals_for_avg = self._avg_field(home_recent, "goals_for")
            home_goals_against_avg = self._avg_field(home_recent, "goals_against")
            away_goals_for_avg = self._avg_field(away_recent, "goals_for")
            away_goals_against_avg = self._avg_field(away_recent, "goals_against")

            context = MatchContext(
                source="sstats_form",
                payload={
                    "mode": "team_form",
                    "home_recent": home_recent,
                    "away_recent": away_recent,
                },
                expected_home=round(expected_home, 3),
                expected_away=round(expected_away, 3),
                home_win_probability=round(home_prob, 4),
                away_win_probability=round(away_prob, 4),
                confidence=round(confidence, 2),
                details={
                    "sstats_mode": "team_form",
                    "home_team_key": home_key,
                    "away_team_key": away_key,
                    "home_recent_count": len(home_recent),
                    "away_recent_count": len(away_recent),
                    "league_overlap_count": league_overlap,
                    "home_goals_for_avg": round(home_goals_for_avg, 3) if home_goals_for_avg is not None else None,
                    "home_goals_against_avg": round(home_goals_against_avg, 3) if home_goals_against_avg is not None else None,
                    "away_goals_for_avg": round(away_goals_for_avg, 3) if away_goals_for_avg is not None else None,
                    "away_goals_against_avg": round(away_goals_against_avg, 3) if away_goals_against_avg is not None else None,
                },
            )
            contexts[match.match_key] = context

            if len(preview["team_form_examples"]) < 10:
                preview["team_form_examples"].append(
                    {
                        "match_key": match.match_key,
                        "match_home": match.home_team,
                        "match_away": match.away_team,
                        "home_team_key": home_key,
                        "away_team_key": away_key,
                        "home_recent_count": len(home_recent),
                        "away_recent_count": len(away_recent),
                        "expected_home": round(expected_home, 3),
                        "expected_away": round(expected_away, 3),
                    }
                )

        return contexts

    def _select_recent_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        league_name: str,
        before: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []

        same_league: list[dict[str, Any]] = []
        fallback: list[dict[str, Any]] = []
        for row in rows:
            start = row.get("start")
            if not isinstance(start, datetime):
                continue
            if start >= before:
                continue
            if self._league_related(league_name, str(row.get("league") or "")):
                same_league.append(row)
            else:
                fallback.append(row)

        selected = same_league[:limit]
        if len(selected) < limit:
            selected.extend(fallback[: max(0, limit - len(selected))])
        return selected

    def _blend_expected_goals(
        self,
        home_recent: list[dict[str, Any]],
        away_recent: list[dict[str, Any]],
        *,
        side: str,
    ) -> float | None:
        if not home_recent or not away_recent:
            return None

        if side == "home":
            attack = self._weighted_average([row.get("goals_for") for row in home_recent])
            opponent_defense = self._weighted_average([row.get("goals_against") for row in away_recent])
            home_split = self._weighted_average([row.get("goals_for") for row in home_recent if row.get("home") is True])
            away_def_split = self._weighted_average([row.get("goals_against") for row in away_recent if row.get("home") is False])
        else:
            attack = self._weighted_average([row.get("goals_for") for row in away_recent])
            opponent_defense = self._weighted_average([row.get("goals_against") for row in home_recent])
            home_split = self._weighted_average([row.get("goals_for") for row in away_recent if row.get("home") is False])
            away_def_split = self._weighted_average([row.get("goals_against") for row in home_recent if row.get("home") is True])

        values: list[tuple[float, float]] = []
        if attack is not None:
            values.append((attack, 0.45))
        if opponent_defense is not None:
            values.append((opponent_defense, 0.35))
        if home_split is not None:
            values.append((home_split, 0.12))
        if away_def_split is not None:
            values.append((away_def_split, 0.08))
        if not values:
            return None

        total_weight = sum(weight for _, weight in values)
        value = sum(v * weight for v, weight in values) / total_weight
        return clamp(value, 0.25, 3.75)

    @staticmethod
    def _weighted_average(values: list[float | None]) -> float | None:
        clean = [float(v) for v in values if v is not None]
        if not clean:
            return None
        weights = [1.0 / (index + 1) ** 0.6 for index in range(len(clean))]
        total_weight = sum(weights)
        return sum(value * weight for value, weight in zip(clean, weights, strict=False)) / total_weight

    @staticmethod
    def _avg_field(rows: list[dict[str, Any]], key: str) -> float | None:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        if not values:
            return None
        return sum(values) / len(values)

    def _resolve_team_key(
        self,
        team_name: str,
        canonical_keys: set[str],
        cache: dict[str, str | None],
    ) -> str | None:
        raw = str(team_name or "")
        if raw in cache:
            return cache[raw]

        canonical = canonicalize_team_name(raw)
        if canonical in canonical_keys:
            cache[raw] = canonical
            return canonical

        best_key: str | None = None
        best_score = 0.0
        for key in canonical_keys:
            score = team_similarity(raw, key)
            if score > best_score:
                best_score = score
                best_key = key

        resolved = best_key if best_key is not None and best_score >= 0.9 else None
        cache[raw] = resolved
        return resolved

    @staticmethod
    def _count_league_matches(rows: list[dict[str, Any]], league_name: str) -> int:
        return sum(1 for row in rows if SStatsContextProvider._league_related(league_name, str(row.get("league") or "")))

    @staticmethod
    def _league_related(a: str, b: str) -> bool:
        return leagues_related(a, b)

    def _row_to_context(self, row: dict[str, Any]) -> MatchContext:
        expected_home = self._first_float(
            row,
            ["ExpectedGoalsHome", "xGHome", "CalculatedXgHome", "homeXg", "home_xg"],
        )
        expected_away = self._first_float(
            row,
            ["ExpectedGoalsAway", "xGAway", "CalculatedXgAway", "awayXg", "away_xg"],
        )

        home_prob, away_prob = self._extract_win_probabilities(row)
        if expected_home is None and home_prob is not None:
            expected_home = home_prob * 2.4
        if expected_away is None and away_prob is not None:
            expected_away = away_prob * 2.4

        confidence = 58.0
        if expected_home is not None and expected_away is not None:
            confidence += 6.0
        if home_prob is not None and away_prob is not None:
            confidence += 4.0
        confidence = clamp(confidence, 56.0, 72.0)

        return MatchContext(
            source="sstats",
            payload=row,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "sstats_home_win_probability": home_prob,
                "sstats_away_win_probability": away_prob,
                "has_expected_goals": expected_home is not None and expected_away is not None,
            },
        )

    def _extract_win_probabilities(self, row: dict[str, Any]) -> tuple[float | None, float | None]:
        markets = row.get("odds")
        if not isinstance(markets, list):
            return None, None

        selected: list[dict[str, Any]] | None = None
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = market.get("marketId")
            market_name = str(market.get("marketName") or "").strip().lower()
            odds = market.get("odds")
            if not isinstance(odds, list):
                continue
            if market_id == 1 or market_name in {"1x2", "match odds", "full time result"}:
                selected = [item for item in odds if isinstance(item, dict)]
                break

        if not selected:
            return None, None

        home_odds = None
        away_odds = None
        for item in selected:
            name = str(item.get("name") or "").strip().lower()
            value = self._to_float(item.get("value"))
            if value is None or value <= 1.0:
                continue
            if name == "home":
                home_odds = value
            elif name == "away":
                away_odds = value

        home_prob = (1.0 / home_odds) if home_odds else None
        away_prob = (1.0 / away_odds) if away_odds else None
        return home_prob, away_prob

    @staticmethod
    def _extract_team_name(row: dict[str, Any], side: str) -> str:
        nested_key = "homeTeam" if side == "home" else "awayTeam"
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            name = nested.get("name")
            if name:
                return str(name).strip()

        fallback_keys = (
            ["HomeTeam", "home", "home_name", "Home", "team_home", "homeTeamName"]
            if side == "home"
            else ["AwayTeam", "away", "away_name", "Away", "team_away", "awayTeamName"]
        )
        for key in fallback_keys:
            value = row.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_league_name(row: dict[str, Any]) -> str:
        season = row.get("season")
        if isinstance(season, dict):
            league = season.get("league")
            if isinstance(league, dict):
                name = league.get("name")
                if name:
                    return str(name).strip()

        for key in ["League", "league", "Tournament", "CompetitionName", "competition"]:
            value = row.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_start(row: dict[str, Any]) -> Any | None:
        for key in ["date", "Date", "GameStart", "StartTime", "datetime", "MatchDate"]:
            value = row.get(key)
            if value not in (None, ""):
                try:
                    return parse_datetime(str(value))
                except Exception:
                    continue
        return None

    @staticmethod
    def _extract_result(row: dict[str, Any], side: str) -> float | None:
        keys = ["homeResult", "homeFTResult", "HomeScore"] if side == "home" else ["awayResult", "awayFTResult", "AwayScore"]
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except Exception:
                continue
        return None

    @staticmethod
    def _first_float(payload: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            value = payload.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except Exception:
                continue
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None
