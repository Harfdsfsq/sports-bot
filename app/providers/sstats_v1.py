from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import (
    canonicalize_team_name,
    clamp,
    leagues_related,
    parse_datetime,
    score_event_match,
)

UTC = timezone.utc


class SStatsContextProvider:
    """Clean SStats.net v1 provider.

    Based on the uploaded SStats OpenAPI spec:
      - GET /Games/list
      - GET /Games/last-games-stats
      - GET /Games/glicko/{id}
      - GET /Games/profits

    This provider is deliberately only SStats. It does not call Bzzoiro internally;
    Bzzoiro v2 is a separate provider in app.providers.bzzoiro_v2.
    """

    VERSION = "sstats-v1-clean-2026-04-29"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = os.getenv("SSTATS_API_KEY") or getattr(settings, "sstats_api_key", None)
        self.base_url = (os.getenv("SSTATS_BASE_URL") or "https://api.sstats.net").rstrip("/")
        self.timeout = float(os.getenv("SSTATS_TIMEOUT_SECONDS") or getattr(settings, "sstats_timeout_seconds", 25.0) or 25.0)
        self.max_http_requests = max(
            0,
            int(float(
                os.getenv("SSTATS_MAX_HTTP_REQUESTS_PER_RUN")
                or os.getenv("SSTATS_REQUESTS_MAX_PER_RUN")
                or os.getenv("SSTATS_PER_RUN_MAX")
                or getattr(settings, "sstats_requests_max_per_run", 150)
                or 150
            )),
        )
        self.lookback_days = max(7, int(float(os.getenv("SSTATS_LOOKBACK_DAYS") or getattr(settings, "sstats_lookback_days", 30) or 30)))
        self.recent_limit = min(100, max(5, int(float(os.getenv("SSTATS_RECENT_MATCHES") or getattr(settings, "sstats_recent_matches", 10) or 10))))
        self.chunk_days = max(1, int(float(os.getenv("SSTATS_REQUEST_CHUNK_DAYS", "7") or 7)))
        self.fetch_last_games_stats = self._env_bool("SSTATS_FETCH_LAST_GAMES_STATS", True)
        self.fetch_glicko = self._env_bool("SSTATS_FETCH_GLICKO", True)
        self.fetch_profits = self._env_bool("SSTATS_FETCH_PROFITS", False)
        self.shortlist_detail_limit = max(0, int(float(os.getenv("SSTATS_DETAIL_MATCH_LIMIT", "80") or 80)))

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "provider_version": self.VERSION,
            "api_version": "v1",
            "enabled": bool(self.settings.enable_sstats_context),
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "retry_attempts": 0,
            "max_http_requests_per_run": self.max_http_requests,
            "budget_exhausted": False,
            "target_matches": 0,
            "games_list_rows_fetched": 0,
            "direct_contexts_built": 0,
            "team_form_contexts_built": 0,
            "last_games_stats_fetched": 0,
            "glicko_fetched": 0,
            "profits_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
            "unmatched_after_alias": 0,
            "confirmation_added": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_url": None,
            "last_error": None,
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {
            "sample_rows": [],
            "matched_examples": [],
            "team_form_examples": [],
            "detail_examples": [],
            "unmatched_rows": [],
        }

        if not self.settings.enable_sstats_context or not self.api_key:
            return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == "soccer"]
        if not soccer_matches:
            return {}, stats, preview
        stats["target_matches"] = len(soccer_matches)

        now_utc = datetime.now(UTC)
        min_dt = min(m.commence_time for m in soccer_matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in soccer_matches).astimezone(UTC)
        from_date = (min(now_utc, min_dt) - timedelta(days=self.lookback_days)).date().isoformat()
        to_date = max(now_utc, max_dt).date().isoformat()

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            rows = await self._fetch_games_list(client, from_date, to_date, stats)
            stats["games_list_rows_fetched"] = len(rows)
            preview["sample_rows"] = rows[:3]

            contexts, matched = self._direct_match_contexts(soccer_matches, rows, stats, preview)
            fallback_contexts = self._team_form_contexts(soccer_matches, rows, preview)
            for key, ctx in fallback_contexts.items():
                if key not in contexts:
                    contexts[key] = ctx
                    stats["team_form_contexts_built"] += 1

            detail_targets = self._detail_targets(matched, soccer_matches)
            detail_targets = detail_targets[: self.shortlist_detail_limit] if self.shortlist_detail_limit > 0 else detail_targets
            await self._enrich_with_details(client, contexts, detail_targets, stats, preview)

        stats["contexts_built"] = len(contexts)
        stats["confirmation_added"] = len(contexts)
        stats["unmatched_after_alias"] = int(stats.get("unmatched_rows", 0) or 0)
        return contexts, stats, preview

    async def _fetch_games_list(self, client: httpx.AsyncClient, from_date: str, to_date: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        start = parse_datetime(from_date).date()
        end = parse_datetime(to_date).date()
        cursor = start
        while cursor <= end and not self._budget_exhausted(stats):
            window_end = min(end, cursor + timedelta(days=self.chunk_days - 1))
            offset = 0
            limit = 1000
            while not self._budget_exhausted(stats):
                payload = await self._get_json(client, "/Games/list", {
                    "From": cursor.isoformat(),
                    "To": window_end.isoformat(),
                    "Limit": limit,
                    "Offset": offset,
                    "apikey": self.api_key,
                }, stats)
                batch = self._rows(payload)
                if not batch:
                    break
                for row in batch:
                    sig = (
                        row.get("id") or row.get("Id"),
                        row.get("flashId") or row.get("FlashId"),
                        row.get("date") or row.get("Date"),
                        self._team(row, "home"),
                        self._team(row, "away"),
                    )
                    if sig in seen:
                        continue
                    seen.add(sig)
                    rows.append(row)
                if len(batch) < limit:
                    break
                offset += len(batch)
            cursor = window_end + timedelta(days=1)
        return rows

    def _direct_match_contexts(self, matches: list[Match], rows: list[dict[str, Any]], stats: dict[str, Any], preview: dict[str, Any]) -> tuple[dict[str, MatchContext], dict[str, dict[str, Any]]]:
        contexts: dict[str, MatchContext] = {}
        matched: dict[str, dict[str, Any]] = {}
        best_scores: dict[str, float] = {}
        earliest = min(m.commence_time for m in matches).astimezone(UTC) - timedelta(hours=18)
        latest = max(m.commence_time for m in matches).astimezone(UTC) + timedelta(hours=18)
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = max(float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8), 24.0)

        for row in rows:
            home = self._team(row, "home")
            away = self._team(row, "away")
            league = self._league(row)
            start = self._start(row)
            if not home or not away or start is None:
                stats["unmatched_rows"] += 1
                continue
            if start < earliest or start > latest:
                continue
            best_match: Match | None = None
            best_score = 0.0
            best_quality: str | None = None
            for match in matches:
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
                    exact_tolerance_hours=exact_tol,
                    fuzzy_tolerance_hours=fuzzy_tol,
                )
                if score > best_score:
                    best_score, best_quality, best_match = score, quality, match
            if best_match is None or best_quality is None:
                stats["unmatched_rows"] += 1
                continue
            if best_quality == "fuzzy" and league and not leagues_related(best_match.league_name, league):
                stats["unmatched_rows"] += 1
                continue
            min_score = 74.0 if best_quality == "fuzzy" else 64.0
            if best_score < min_score:
                stats["unmatched_rows"] += 1
                continue
            ctx = self._row_to_context(row, source="sstats_direct", quality=best_quality)
            if ctx is None:
                continue
            if best_scores.get(best_match.match_key, -1.0) >= best_score:
                continue
            contexts[best_match.match_key] = ctx
            matched[best_match.match_key] = {"match": best_match, "row": row, "quality": best_quality, "score": best_score}
            best_scores[best_match.match_key] = best_score
            stats["direct_contexts_built"] += 1
            if best_quality == "exact":
                stats["matched_exact"] += 1
            elif best_quality == "loose":
                stats["matched_loose"] += 1
            elif best_quality == "fuzzy":
                stats["matched_fuzzy"] += 1
            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append({
                    "match_key": best_match.match_key,
                    "match_home": best_match.home_team,
                    "match_away": best_match.away_team,
                    "row_id": self._game_id(row),
                    "row_home": home,
                    "row_away": away,
                    "league": league,
                    "quality": best_quality,
                    "score": round(best_score, 2),
                })
        return contexts, matched

    def _team_form_contexts(self, matches: list[Match], rows: list[dict[str, Any]], preview: dict[str, Any]) -> dict[str, MatchContext]:
        team_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            start = self._start(row)
            home = self._team(row, "home")
            away = self._team(row, "away")
            hg = self._goals(row, "home")
            ag = self._goals(row, "away")
            if start is None or not home or not away or hg is None or ag is None:
                continue
            hkey = canonicalize_team_name(home)
            akey = canonicalize_team_name(away)
            if hkey:
                team_rows[hkey].append({"start": start, "gf": float(hg), "ga": float(ag), "home": True})
            if akey:
                team_rows[akey].append({"start": start, "gf": float(ag), "ga": float(hg), "home": False})
        for vals in team_rows.values():
            vals.sort(key=lambda x: x["start"], reverse=True)
        contexts: dict[str, MatchContext] = {}
        for match in matches:
            home_rows = team_rows.get(canonicalize_team_name(match.home_team), [])[: self.recent_limit]
            away_rows = team_rows.get(canonicalize_team_name(match.away_team), [])[: self.recent_limit]
            if len(home_rows) < 3 or len(away_rows) < 3:
                continue
            h_for = sum(x["gf"] for x in home_rows) / len(home_rows)
            h_against = sum(x["ga"] for x in home_rows) / len(home_rows)
            a_for = sum(x["gf"] for x in away_rows) / len(away_rows)
            a_against = sum(x["ga"] for x in away_rows) / len(away_rows)
            expected_home = clamp((h_for + a_against) / 2.0, 0.25, 3.75)
            expected_away = clamp((a_for + h_against) / 2.0, 0.25, 3.75)
            confidence = clamp(50.0 + min(len(home_rows), len(away_rows)) * 0.8, 52.0, 62.0)
            contexts[match.match_key] = MatchContext(
                source="sstats_form_v1",
                payload={"home_rows": home_rows, "away_rows": away_rows},
                expected_home=round(expected_home, 3),
                expected_away=round(expected_away, 3),
                confidence=confidence,
                details={
                    "sstats_api_version": "v1",
                    "sstats_mode": "team_form",
                    "home_recent_sample": len(home_rows),
                    "away_recent_sample": len(away_rows),
                    "home_gf_avg": round(h_for, 3),
                    "home_ga_avg": round(h_against, 3),
                    "away_gf_avg": round(a_for, 3),
                    "away_ga_avg": round(a_against, 3),
                },
            )
            if len(preview["team_form_examples"]) < 6:
                preview["team_form_examples"].append({
                    "match_key": match.match_key,
                    "expected_home": round(expected_home, 3),
                    "expected_away": round(expected_away, 3),
                    "home_sample": len(home_rows),
                    "away_sample": len(away_rows),
                })
        return contexts

    async def _enrich_with_details(self, client: httpx.AsyncClient, contexts: dict[str, MatchContext], targets: list[dict[str, Any]], stats: dict[str, Any], preview: dict[str, Any]) -> None:
        for item in targets:
            if self._budget_exhausted(stats):
                return
            match = item["match"]
            row = item["row"]
            ctx = contexts.get(match.match_key)
            if ctx is None:
                continue
            game_id = self._game_id(row)
            if not game_id:
                continue
            details: dict[str, Any] = {}
            if self.fetch_last_games_stats and not self._budget_exhausted(stats):
                payload = await self._get_json(client, "/Games/last-games-stats", {"gameId": game_id, "limit": self.recent_limit, "apikey": self.api_key}, stats)
                if payload is not None:
                    details["last_games_stats"] = payload
                    stats["last_games_stats_fetched"] += 1
            if self.fetch_glicko and not self._budget_exhausted(stats):
                payload = await self._get_json(client, f"/Games/glicko/{game_id}", {"apikey": self.api_key}, stats)
                if payload is not None:
                    details["glicko"] = payload
                    stats["glicko_fetched"] += 1
            if self.fetch_profits and not self._budget_exhausted(stats):
                payload = await self._get_json(client, "/Games/profits", {"gameId": game_id, "limit": self.recent_limit, "apikey": self.api_key}, stats)
                if payload is not None:
                    details["profits"] = payload
                    stats["profits_fetched"] += 1
            if not details:
                continue
            enriched = self._context_from_details(ctx, details)
            contexts[match.match_key] = enriched
            if len(preview["detail_examples"]) < 8:
                preview["detail_examples"].append({
                    "match_key": match.match_key,
                    "game_id": game_id,
                    "has_last_games_stats": "last_games_stats" in details,
                    "has_glicko": "glicko" in details,
                    "expected_home": enriched.expected_home,
                    "expected_away": enriched.expected_away,
                    "confidence": enriched.confidence,
                })

    def _context_from_details(self, base: MatchContext, details: dict[str, Any]) -> MatchContext:
        last = self._unwrap(details.get("last_games_stats"))
        glicko = self._unwrap(details.get("glicko"))
        expected_home = base.expected_home
        expected_away = base.expected_away
        home_prob = base.home_win_probability
        away_prob = base.away_win_probability
        confidence = base.confidence

        if isinstance(last, dict):
            hxg = self._nested_first_float(last, ["xg", "xG", "expectedGoals", "expected_goals"], side="home")
            axg = self._nested_first_float(last, ["xg", "xG", "expectedGoals", "expected_goals"], side="away")
            hgf = self._nested_first_float(last, ["goalsFor", "goals_for", "goalsScored", "scored"], side="home")
            aga = self._nested_first_float(last, ["goalsAgainst", "goals_against", "conceded"], side="away")
            agf = self._nested_first_float(last, ["goalsFor", "goals_for", "goalsScored", "scored"], side="away")
            hga = self._nested_first_float(last, ["goalsAgainst", "goals_against", "conceded"], side="home")
            if hxg is not None and axg is not None:
                expected_home = clamp(hxg, 0.15, 4.5)
                expected_away = clamp(axg, 0.15, 4.5)
                confidence += 5.0
            elif hgf is not None and aga is not None and agf is not None and hga is not None:
                expected_home = clamp((hgf + aga) / 2.0, 0.15, 4.5)
                expected_away = clamp((agf + hga) / 2.0, 0.15, 4.5)
                confidence += 3.0

        if isinstance(glicko, dict):
            home_prob = self._first_float(glicko, ["homeWinProbability", "home_win_probability", "probHome", "prob_home", "p1"])
            away_prob = self._first_float(glicko, ["awayWinProbability", "away_win_probability", "probAway", "prob_away", "p2"])
            if home_prob is not None and home_prob > 1.0:
                home_prob /= 100.0
            if away_prob is not None and away_prob > 1.0:
                away_prob /= 100.0
            if home_prob is not None:
                home_prob = clamp(home_prob, 0.02, 0.96)
            if away_prob is not None:
                away_prob = clamp(away_prob, 0.02, 0.96)
            confidence += 3.0

        new_details = dict(base.details or {})
        new_details.update({
            "sstats_api_version": "v1",
            "sstats_mode": "direct_plus_details",
            "sstats_has_last_games_stats": "last_games_stats" in details,
            "sstats_has_glicko": "glicko" in details,
            "sstats_has_profits": "profits" in details,
        })
        return MatchContext(
            source="sstats_v1",
            payload={"base": base.payload, "details": details},
            expected_home=round(expected_home, 3) if expected_home is not None else None,
            expected_away=round(expected_away, 3) if expected_away is not None else None,
            home_win_probability=home_prob,
            away_win_probability=away_prob,
            confidence=clamp(confidence, 54.0, 72.0),
            details=new_details,
        )

    def _detail_targets(self, matched: dict[str, dict[str, Any]], matches: list[Match]) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        values = list(matched.values())
        values.sort(key=lambda item: (0 if getattr(item["match"], "tier", "mid") == "top" else 1, abs((item["match"].commence_time - now).total_seconds())))
        return values

    async def _get_json(self, client: httpx.AsyncClient, path: str, params: dict[str, Any], stats: dict[str, Any]) -> Any | None:
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            if self._budget_exhausted(stats):
                return None
            if attempt > 0:
                stats["retry_attempts"] += 1
                await asyncio.sleep(0.5 * attempt)
            stats["requests"] += 1
            stats["last_url"] = url
            try:
                response = await client.get(url, params=params)
            except Exception as exc:
                stats["last_error"] = f"{exc.__class__.__name__}: {exc}"
                if attempt >= 2:
                    stats["response_errors"] += 1
                    return None
                continue
            stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:1600]
            if response.status_code == 200:
                try:
                    payload = response.json()
                    shape = self._shape(payload)
                    if shape not in stats["payload_shapes"]:
                        stats["payload_shapes"].append(shape)
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

    def _budget_exhausted(self, stats: dict[str, Any]) -> bool:
        if self.max_http_requests <= 0:
            return False
        if int(stats.get("requests", 0) or 0) >= self.max_http_requests:
            stats["budget_exhausted"] = True
            stats["last_error"] = "budget_exhausted"
            return True
        return False

    def _row_to_context(self, row: dict[str, Any], *, source: str, quality: str | None) -> MatchContext | None:
        home_prob = self._first_float(row, ["HomeWinProbability", "homeWinProbability", "probHome", "Winner1Probability"])
        away_prob = self._first_float(row, ["AwayWinProbability", "awayWinProbability", "probAway", "Winner2Probability"])
        if home_prob is not None and home_prob > 1.0:
            home_prob /= 100.0
        if away_prob is not None and away_prob > 1.0:
            away_prob /= 100.0
        home_xg = self._first_float(row, ["HomeXg", "homeXg", "xgHome", "expectedHomeGoals"])
        away_xg = self._first_float(row, ["AwayXg", "awayXg", "xgAway", "expectedAwayGoals"])
        hg = self._goals(row, "home")
        ag = self._goals(row, "away")
        expected_home = home_xg if home_xg is not None else float(hg) if hg is not None else None
        expected_away = away_xg if away_xg is not None else float(ag) if ag is not None else None
        if expected_home is None and expected_away is None and home_prob is None and away_prob is None:
            return None
        confidence = 56.0
        if expected_home is not None and expected_away is not None:
            confidence += 3.0
        if home_prob is not None and away_prob is not None:
            confidence += 2.0
        if quality == "fuzzy":
            confidence -= 5.0
        elif quality == "loose":
            confidence -= 2.0
        return MatchContext(
            source=source,
            payload=row,
            expected_home=round(clamp(expected_home, 0.15, 4.5), 3) if expected_home is not None else None,
            expected_away=round(clamp(expected_away, 0.15, 4.5), 3) if expected_away is not None else None,
            home_win_probability=clamp(home_prob, 0.02, 0.96) if home_prob is not None else None,
            away_win_probability=clamp(away_prob, 0.02, 0.96) if away_prob is not None else None,
            confidence=clamp(confidence, 50.0, 66.0),
            details={"sstats_api_version": "v1", "sstats_mode": source, "sstats_match_quality": quality, "sstats_game_id": self._game_id(row)},
        )

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        payload = SStatsContextProvider._unwrap(payload)
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "results", "items", "games"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if isinstance(payload, dict):
            for key in ("data", "result", "value"):
                if key in payload and payload.get(key) not in (None, ""):
                    return payload.get(key)
        return payload

    @staticmethod
    def _shape(payload: Any) -> str:
        if isinstance(payload, list):
            return f"list:{len(payload)}"
        if isinstance(payload, dict):
            return "dict:" + ",".join(sorted(str(k) for k in list(payload)[:8]))
        return type(payload).__name__

    @staticmethod
    def _game_id(row: dict[str, Any]) -> str:
        return str(row.get("id") or row.get("Id") or row.get("gameId") or row.get("GameId") or "").strip()

    @staticmethod
    def _league(row: dict[str, Any]) -> str:
        league = row.get("league") or row.get("League")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or "")
        return str(row.get("leagueName") or row.get("LeagueName") or row.get("league") or "")

    @staticmethod
    def _team(row: dict[str, Any], side: str) -> str:
        keys = [
            f"{side}TeamName", f"{side.capitalize()}TeamName", f"{side}_team", f"{side}Team", f"{side}",
            "Team1" if side == "home" else "Team2",
            "team1" if side == "home" else "team2",
        ]
        for key in keys:
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("Name")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _start(row: dict[str, Any]) -> datetime | None:
        for key in ("date", "Date", "start", "Start", "gameDate", "GameDate", "kickoff", "Kickoff"):
            value = row.get(key)
            if value not in (None, ""):
                try:
                    dt = parse_datetime(value)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt.astimezone(UTC)
                except Exception:
                    continue
        return None

    @staticmethod
    def _goals(row: dict[str, Any], side: str) -> int | None:
        keys = [
            f"{side}Goals", f"{side.capitalize()}Goals", f"{side}Score", f"{side.capitalize()}Score",
            "Score1" if side == "home" else "Score2",
            "score1" if side == "home" else "score2",
            "home" if side == "home" else "away",
        ]
        for key in keys:
            value = row.get(key)
            try:
                if value not in (None, "") and not isinstance(value, dict):
                    return int(float(value))
            except Exception:
                continue
        return None

    @classmethod
    def _first_float(cls, row: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            try:
                value = row.get(key)
                if value not in (None, ""):
                    return float(value)
            except Exception:
                continue
        return None

    @classmethod
    def _nested_first_float(cls, payload: Any, keys: list[str], *, side: str) -> float | None:
        side_aliases = [side, side.capitalize(), "team1" if side == "home" else "team2", "Team1" if side == "home" else "Team2"]
        if isinstance(payload, dict):
            for alias in side_aliases:
                obj = payload.get(alias)
                if isinstance(obj, dict):
                    val = cls._first_float(obj, keys)
                    if val is not None:
                        return val
            val = cls._first_float(payload, [f"{side}_{k}" for k in keys] + [f"{side}{k}" for k in keys])
            if val is not None:
                return val
            for child in payload.values():
                val = cls._nested_first_float(child, keys, side=side)
                if val is not None:
                    return val
        elif isinstance(payload, list):
            for item in payload:
                val = cls._nested_first_float(item, keys, side=side)
                if val is not None:
                    return val
        return None

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
