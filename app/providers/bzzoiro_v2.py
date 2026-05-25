from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
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


class BzzoiroContextProvider:
    """Bzzoiro Sports Data API v2 context provider.

    Uses the uploaded OpenAPI v2 endpoints:
      - GET /api/v2/events/
      - GET /api/v2/events/{id}/odds/
      - GET /api/v2/events/{id}/stats/
      - GET /api/v2/events/{id}/metadata/

    There is intentionally no per-run/provider limit in this provider. The only
    limits are endpoint pagination exhaustion, HTTP timeout/retry, and optional
    safety envs if explicitly enabled.
    """

    VERSION = "bzzoiro-v2-clean-2026-04-29"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
        self.api_key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
        self.timeout = float(os.getenv("BZZOIRO_TIMEOUT_SECONDS") or getattr(settings, "bzzoiro_timeout_seconds", 20.0) or 20.0)
        self.retries = max(0, int(float(os.getenv("BZZOIRO_REQUEST_RETRIES", "2") or 2)))
        self.retry_backoff = max(0.0, float(os.getenv("BZZOIRO_RETRY_BACKOFF_SECONDS", "0.75") or 0.75))
        self.page_size = min(200, max(1, int(float(os.getenv("BZZOIRO_V2_PAGE_SIZE", "200") or 200))))
        self.max_events = max(0, int(float(os.getenv("BZZOIRO_V2_MAX_EVENTS", "0") or 0)))  # 0 = no cap
        self.fetch_event_odds = self._env_bool("BZZOIRO_V2_FETCH_EVENT_ODDS", True)
        self.fetch_event_stats = self._env_bool("BZZOIRO_V2_FETCH_EVENT_STATS", True)
        self.fetch_event_metadata = self._env_bool("BZZOIRO_V2_FETCH_EVENT_METADATA", False)
        self.enforce_context_limit = self._env_bool("BZZOIRO_ENFORCE_CONTEXT_LIMIT", False)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "provider_version": self.VERSION,
            "api_version": "v2",
            "enabled": bool(self.api_key),
            "api_key_present": bool(self.api_key),
            "request_limit_removed": True,
            "context_limit_enforced": self.enforce_context_limit,
            "base_url": self.base_url,
            "requests": 0,
            "response_errors": 0,
            "retry_attempts": 0,
            "events_fetched": 0,
            "event_matches": 0,
            "event_odds_fetched": 0,
            "event_stats_fetched": 0,
            "event_metadata_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "event_rejected_low_score": 0,
            "event_rejected_league_mismatch": 0,
            "event_rejected_no_quality": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_url": None,
            "last_error": None,
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_events": [], "matched_examples": [], "unmatched_examples": []}
        if not self.api_key:
            return {}, stats, preview

        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview
        soccer_matches = self._prioritize_matches(soccer_matches)
        if self.enforce_context_limit:
            limit = max(0, int(float(os.getenv("BZZOIRO_CONTEXT_MATCH_LIMIT", "0") or 0)))
            if limit > 0:
                soccer_matches = soccer_matches[:limit]

        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)
        headers = {"Authorization": f"Token {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            events = await self._fetch_events(client, headers, min_dt.date().isoformat(), max_dt.date().isoformat(), stats)
            stats["events_fetched"] = len(events)
            preview["sample_events"] = events[:3]

            contexts: dict[str, MatchContext] = {}
            detail_cache: dict[int, dict[str, Any]] = {}
            for match in soccer_matches:
                event, quality, score, diag = self._match_event(match, events)
                if not event:
                    stats["event_rejected_no_quality"] += 1
                    if len(preview["unmatched_examples"]) < 8:
                        preview["unmatched_examples"].append({
                            "match_key": match.match_key,
                            "home": match.home_team,
                            "away": match.away_team,
                            "league": match.league_name,
                            "best_event": diag,
                        })
                    continue
                event_id = self._to_int(event.get("id"))
                details: dict[str, Any] = {"event": event, "odds": None, "stats": None, "metadata": None}
                if event_id is not None:
                    details = detail_cache.get(event_id) or details
                    if event_id not in detail_cache:
                        if self.fetch_event_odds:
                            details["odds"] = await self._get_json(client, f"/events/{event_id}/odds/", headers, {}, stats)
                            if details["odds"] is not None:
                                stats["event_odds_fetched"] += 1
                        if self.fetch_event_stats:
                            details["stats"] = await self._get_json(client, f"/events/{event_id}/stats/", headers, {}, stats)
                            if details["stats"] is not None:
                                stats["event_stats_fetched"] += 1
                        if self.fetch_event_metadata:
                            details["metadata"] = await self._get_json(client, f"/events/{event_id}/metadata/", headers, {}, stats)
                            if details["metadata"] is not None:
                                stats["event_metadata_fetched"] += 1
                        detail_cache[event_id] = details

                context = self._event_to_context(details, quality)
                if context is None:
                    stats["event_rejected_no_quality"] += 1
                    continue
                contexts[match.match_key] = context
                stats["contexts_built"] = len(contexts)
                stats["event_matches"] += 1
                if quality == "exact":
                    stats["matched_exact"] += 1
                elif quality == "loose":
                    stats["matched_loose"] += 1
                elif quality == "fuzzy":
                    stats["matched_fuzzy"] += 1
                if len(preview["matched_examples"]) < 10:
                    preview["matched_examples"].append({
                        "match_key": match.match_key,
                        "match_home": match.home_team,
                        "match_away": match.away_team,
                        "event_id": event.get("id"),
                        "event_home": event.get("home_team"),
                        "event_away": event.get("away_team"),
                        "event_score": round(score, 2),
                        "quality": quality,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                        "confidence": context.confidence,
                    })

        return contexts, stats, preview

    async def _fetch_events(self, client: httpx.AsyncClient, headers: dict[str, str], date_from: str, date_to: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._get_json(
                client,
                "/events/",
                headers,
                {"date_from": date_from, "date_to": date_to, "status": "notstarted", "limit": self.page_size, "offset": offset},
                stats,
            )
            batch = self._rows(payload)
            if not batch:
                break
            rows.extend(batch)
            if self.max_events > 0 and len(rows) >= self.max_events:
                return rows[: self.max_events]
            if len(batch) < self.page_size:
                break
            offset += self.page_size
        return rows

    async def _get_json(self, client: httpx.AsyncClient, path: str, headers: dict[str, str], params: dict[str, Any], stats: dict[str, Any]) -> Any | None:
        url = f"{self.base_url}{path}"
        for attempt in range(self.retries + 1):
            if attempt > 0:
                stats["retry_attempts"] += 1
                if self.retry_backoff > 0:
                    await asyncio.sleep(self.retry_backoff * attempt)
            stats["requests"] += 1
            stats["last_url"] = url
            try:
                response = await client.get(url, headers=headers, params=params)
            except Exception as exc:
                stats["last_error"] = f"{exc.__class__.__name__}: {exc}"
                if attempt >= self.retries:
                    stats["response_errors"] += 1
                    return None
                continue
            stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:1200]
            if response.status_code == 200:
                stats["last_error"] = None
                try:
                    payload = response.json()
                    stats["payload_shapes"].append(self._shape(payload))
                    return payload
                except Exception as exc:
                    stats["last_error"] = f"json_error:{exc.__class__.__name__}"
                    stats["response_errors"] += 1
                    return None
            stats["last_error"] = f"http_status={response.status_code}"
            if response.status_code not in {408, 409, 425, 429} and response.status_code < 500:
                stats["response_errors"] += 1
                return None
        stats["response_errors"] += 1
        return None

    def _match_event(self, match: Match, events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, float, dict[str, Any] | None]:
        best: dict[str, Any] | None = None
        best_quality: str | None = None
        best_score = 0.0
        best_diag: dict[str, Any] | None = None
        for event in events:
            home_candidates, away_candidates = self._team_candidates(event)
            if not home_candidates or not away_candidates:
                continue
            try:
                start = parse_datetime(event.get("event_date"))
            except Exception:
                continue
            league = self._league_name(event)
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
                exact_tolerance_hours=6.0,
                fuzzy_tolerance_hours=24.0,
            )
            if score > best_score:
                best = event
                best_quality = quality
                best_score = score
                best_diag = {"score": round(score, 2), "quality": quality, "league": league, "home": home_candidates[:3], "away": away_candidates[:3], "start": start.isoformat()}
        if best is None or best_quality is None:
            return None, None, 0.0, best_diag
        min_score = 69.0 if best_quality == "fuzzy" else 64.0
        event_league = self._league_name(best)
        if best_quality == "fuzzy" and event_league and not leagues_related(match.league_name, event_league):
            if best_diag is not None:
                best_diag["accepted"] = False
                best_diag["rejection_reason"] = "league_mismatch"
            return None, None, 0.0, best_diag
        if not event_league:
            min_score += 4.0
        if best_score < min_score:
            if best_diag is not None:
                best_diag["accepted"] = False
                best_diag["rejection_reason"] = "low_score"
                best_diag["required_score"] = min_score
            return None, None, 0.0, best_diag
        return best, best_quality, best_score, best_diag

    def _event_to_context(self, details: dict[str, Any], quality: str | None) -> MatchContext | None:
        event = details.get("event") if isinstance(details.get("event"), dict) else {}
        odds_payload = details.get("odds")
        stats_payload = details.get("stats")
        metadata_payload = details.get("metadata")

        home_price = self._first_float(event, ["odds_home", "home_odds", "home_win_odds"])
        draw_price = self._first_float(event, ["odds_draw", "draw_odds"])
        away_price = self._first_float(event, ["odds_away", "away_odds", "away_win_odds"])
        odds_flat = self._flatten_odds(odds_payload)
        home_price = home_price or self._odds_price(odds_flat, market_aliases={"1x2", "h2h", "match_winner"}, outcome_aliases={"home", "1"})
        draw_price = draw_price or self._odds_price(odds_flat, market_aliases={"1x2", "h2h", "match_winner"}, outcome_aliases={"draw", "x"})
        away_price = away_price or self._odds_price(odds_flat, market_aliases={"1x2", "h2h", "match_winner"}, outcome_aliases={"away", "2"})

        home_prob = away_prob = draw_prob = None
        if home_price and away_price:
            normalized = strip_vig_three_way(home_price, draw_price or 0.0, away_price)
            if normalized is not None:
                home_prob, draw_prob, away_prob = normalized
        if home_prob is None and home_price and away_price:
            raw_home = implied_probability(home_price)
            raw_draw = implied_probability(draw_price) if draw_price else None
            raw_away = implied_probability(away_price)
            denom = sum(x for x in (raw_home, raw_draw, raw_away) if x is not None)
            if denom > 0:
                home_prob = clamp(raw_home / denom, 0.01, 0.95)
                away_prob = clamp(raw_away / denom, 0.01, 0.95)
                draw_prob = clamp((raw_draw or 0.0) / denom, 0.01, 0.60) if raw_draw else None

        expected_home = self._extract_xg(stats_payload, event, side="home")
        expected_away = self._extract_xg(stats_payload, event, side="away")
        over25 = self._two_way_probability(event, odds_flat, 2.5)
        if (expected_home is None or expected_away is None) and over25 is not None:
            total_lambda = self._infer_total_lambda(over25, 2.5)
            if total_lambda is not None:
                share = 0.5
                if home_prob is not None and away_prob is not None and (home_prob + away_prob) > 0:
                    share = clamp(home_prob / (home_prob + away_prob), 0.28, 0.72)
                expected_home = expected_home if expected_home is not None else total_lambda * share
                expected_away = expected_away if expected_away is not None else total_lambda * (1.0 - share)
        if expected_home is not None:
            expected_home = round(clamp(expected_home, 0.15, 4.50), 3)
        if expected_away is not None:
            expected_away = round(clamp(expected_away, 0.15, 4.50), 3)
        if expected_home is None and expected_away is None and home_prob is None and away_prob is None:
            return None

        confidence = 54.0
        if expected_home is not None and expected_away is not None:
            confidence += 5.0
        if home_prob is not None and away_prob is not None:
            confidence += 3.0
        if over25 is not None:
            confidence += 2.0
        if stats_payload is not None:
            confidence += 3.0
        if metadata_payload is not None:
            confidence += 1.0
        if quality == "fuzzy":
            confidence -= 5.0
        elif quality == "loose":
            confidence -= 2.0
        confidence = clamp(confidence, 50.0, 72.0)

        return MatchContext(
            source="bzzoiro_v2",
            payload=details,
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=confidence,
            details={
                "bzzoiro_api_version": "v2",
                "bzzoiro_match_quality": quality,
                "bzzoiro_draw_probability": draw_prob,
                "bzzoiro_over25_probability": over25,
                "bzzoiro_event_id": event.get("id"),
                "bzzoiro_status": event.get("status"),
                "bzzoiro_is_neutral_ground": event.get("is_neutral_ground"),
                "bzzoiro_is_local_derby": event.get("is_local_derby"),
                "bzzoiro_travel_distance_km": event.get("travel_distance_km"),
                "bzzoiro_weather": event.get("weather"),
                "bzzoiro_context_limit_removed": True,
            },
        )

    def _extract_xg(self, stats_payload: Any, event: dict[str, Any], *, side: str) -> float | None:
        keys = [
            f"{side}_xg_live", f"actual_{side}_xg", f"{side}_xg", f"expected_{side}_goals",
            f"pre_match_{side}_xg", f"{side}_expected_goals",
        ]
        value = self._first_float(event, keys)
        if value is not None:
            return value
        return self._nested_first_float(stats_payload, keys + ["xg", "expected_goals"], side=side)

    def _two_way_probability(self, event: dict[str, Any], odds_flat: list[dict[str, Any]], line: float) -> float | None:
        suffix = str(line).replace(".", "")
        over_key = f"odds_over_{suffix}"
        under_key = f"odds_under_{suffix}"
        over_price = self._to_float(event.get(over_key))
        under_price = self._to_float(event.get(under_key))
        over_price = over_price or self._total_price(odds_flat, line, over=True)
        under_price = under_price or self._total_price(odds_flat, line, over=False)
        if over_price and under_price:
            stripped = strip_vig_two_way(over_price, under_price)
            if stripped is not None:
                return stripped[0]
        if over_price:
            return clamp(implied_probability(over_price), 0.01, 0.99)
        if under_price:
            return clamp(1.0 - implied_probability(under_price), 0.01, 0.99)
        return None

    @staticmethod
    def _infer_total_lambda(over_probability: float, line: float) -> float | None:
        target = clamp(float(over_probability), 0.01, 0.99)
        lo, hi = 0.2, 6.0
        k = int(math.floor(line))
        for _ in range(36):
            mid = (lo + hi) / 2.0
            cdf = math.exp(-mid) * sum((mid ** i) / math.factorial(i) for i in range(k + 1))
            over_mid = 1.0 - cdf
            if over_mid < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    @staticmethod
    def _prioritize_matches(matches: list[Match]) -> list[Match]:
        now = datetime.now(UTC)
        return sorted(matches, key=lambda m: (0 if getattr(m, "tier", "mid") == "top" else 1, abs((m.commence_time - now).total_seconds()), m.league_name.lower()))

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "events"):
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
    def _team_candidates(event: dict[str, Any]) -> tuple[list[str], list[str]]:
        def names(prefix: str) -> list[str]:
            out: list[str] = []
            direct = event.get(f"{prefix}_team")
            if isinstance(direct, str) and direct.strip():
                out.append(direct.strip())
            obj = event.get(f"{prefix}_team_obj")
            if isinstance(obj, dict):
                for key in ("name", "short_name", "display_name"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
            return list(dict.fromkeys(out))
        return names("home"), names("away")

    @staticmethod
    def _league_name(event: dict[str, Any]) -> str:
        league = event.get("league")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("league_name") or "")
        return str(event.get("league_name") or event.get("league") or "")

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(float(value))
        except Exception:
            return None

    @classmethod
    def _first_float(cls, row: dict[str, Any], keys: list[str]) -> float | None:
        if not isinstance(row, dict):
            return None
        for key in keys:
            value = cls._to_float(row.get(key))
            if value is not None:
                return value
        return None

    @classmethod
    def _nested_first_float(cls, payload: Any, keys: list[str], *, side: str) -> float | None:
        if isinstance(payload, dict):
            for key in keys:
                value = cls._to_float(payload.get(key))
                if value is not None:
                    return value
            side_obj = payload.get(side)
            if isinstance(side_obj, dict):
                for key in keys:
                    value = cls._to_float(side_obj.get(key))
                    if value is not None:
                        return value
            for value in payload.values():
                found = cls._nested_first_float(value, keys, side=side)
                if found is not None:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = cls._nested_first_float(item, keys, side=side)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _flatten_odds(payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def add_compact(key: str, price: Any, bookmaker: Any = None) -> None:
            low = str(key or "").lower()
            if price in (None, "") or isinstance(price, (dict, list)):
                return
            if low in {"home_win", "home", "home_win_odds"}:
                rows.append({"market": "1x2", "outcome": "home", "price": price, "bookmaker": bookmaker})
            elif low in {"away_win", "away", "away_win_odds"}:
                rows.append({"market": "1x2", "outcome": "away", "price": price, "bookmaker": bookmaker})
            elif low in {"draw", "x", "draw_odds"}:
                rows.append({"market": "1x2", "outcome": "draw", "price": price, "bookmaker": bookmaker})
            elif low in {"btts_yes", "both_teams_to_score_yes"}:
                rows.append({"market": "btts", "outcome": "yes", "price": price, "bookmaker": bookmaker})
            elif low in {"btts_no", "both_teams_to_score_no"}:
                rows.append({"market": "btts", "outcome": "no", "price": price, "bookmaker": bookmaker})
            else:
                import re
                m = re.match(r"^(over|under)[_\- ]?(\d+)(?:[_\- ]?(\d+))?[_\- ]?(?:goals?)?$", low)
                if m:
                    side = m.group(1)
                    if m.group(3):
                        point = cls_float(f"{m.group(2)}.{m.group(3)}")
                    else:
                        token = m.group(2)
                        point = cls_float(f"{token[0]}.{token[1:]}") if len(token) > 1 else cls_float(token)
                    rows.append({"market": "totals", "outcome": side, "line": point, "price": price, "bookmaker": bookmaker})

        def cls_float(value: Any) -> float | None:
            try:
                if value in (None, ""):
                    return None
                return float(str(value).replace(",", "."))
            except Exception:
                return None

        def walk(value: Any, inherited: dict[str, Any] | None = None) -> None:
            inherited = inherited or {}
            if isinstance(value, list):
                for item in value:
                    walk(item, inherited)
            elif isinstance(value, dict):
                # Documented /events/{id}/odds/ compact map.
                compact = value.get("odds") if isinstance(value.get("odds"), dict) else None
                if compact:
                    for key, price in compact.items():
                        add_compact(str(key), price, inherited.get("bookmaker"))
                row = dict(inherited)
                for key in ("market", "market_key", "market_name", "outcome", "name", "selection", "line", "point", "price", "odds", "decimal", "decimal_odds", "bookmaker", "bookmaker_slug", "bookmaker_name"):
                    if key in value and not isinstance(value.get(key), (dict, list)):
                        mapped = "bookmaker" if key in {"bookmaker_slug", "bookmaker_name"} else key
                        row[mapped] = value.get(key)
                if isinstance(value.get("bookmaker"), dict):
                    book = value["bookmaker"]
                    row["bookmaker"] = book.get("slug") or book.get("name") or row.get("bookmaker")
                if isinstance(value.get("market"), dict):
                    market = value["market"]
                    row["market"] = market.get("key") or market.get("name") or row.get("market")
                if any(k in row for k in ("price", "odds", "decimal", "decimal_odds")) and any(k in row for k in ("outcome", "name", "selection")):
                    rows.append(row)
                # Scalar compact children, e.g. {"over_25_goals": 2.04}.
                for key, child in value.items():
                    if not isinstance(child, (dict, list)):
                        add_compact(str(key), child, row.get("bookmaker"))
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        next_inherited = dict(row)
                        if key.lower() in {"1x2", "h2h", "totals", "total", "btts", "over_under", "over_under_15", "over_under_25", "over_under_35"}:
                            next_inherited.setdefault("market", key)
                        elif row.get("market") and key.lower() not in {"outcomes", "selections", "values", "odds", "results", "data"}:
                            next_inherited.setdefault("bookmaker", key)
                        walk(child, next_inherited)
        walk(payload)
        return rows

    @classmethod
    def _odds_price(cls, rows: list[dict[str, Any]], *, market_aliases: set[str], outcome_aliases: set[str]) -> float | None:
        for row in rows:
            market = cls._norm(row.get("market") or row.get("market_key") or row.get("market_name"))
            outcome = cls._norm(row.get("outcome") or row.get("name") or row.get("selection"))
            if market in {cls._norm(x) for x in market_aliases} and outcome in {cls._norm(x) for x in outcome_aliases}:
                return cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds"))
        return None

    @classmethod
    def _total_price(cls, rows: list[dict[str, Any]], line: float, *, over: bool) -> float | None:
        wanted = "over" if over else "under"
        for row in rows:
            market = cls._norm(row.get("market") or row.get("market_key") or row.get("market_name"))
            outcome = cls._norm(row.get("outcome") or row.get("name") or row.get("selection"))
            point = cls._to_float(row.get("line") or row.get("point"))
            if point is None:
                # Some APIs encode line inside selection text: Over 2.5 / Under 2.5.
                text = str(row.get("outcome") or row.get("name") or row.get("selection") or "")
                for token in text.replace(",", ".").split():
                    parsed = cls._to_float(token)
                    if parsed is not None:
                        point = parsed
                        break
            if abs((point or -999.0) - line) > 1e-6:
                continue
            if market not in {"total", "totals", "overunder", "overundergoals", "ou"} and "total" not in market and "over" not in market:
                continue
            if wanted in outcome:
                return cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds"))
        return None

    @staticmethod
    def _norm(value: Any) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
