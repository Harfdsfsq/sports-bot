from __future__ import annotations

"""Runtime fixups for the core source coverage patch.

This module intentionally contains only small, defensive monkey patches for
provider/report edge cases discovered from live run logs:
- SStats v1 result rows use homeResult/awayResult/homeFTResult/awayFTResult.
- SStats team-form must bridge future fixture team IDs to historical form rows.
- The SportLogic query guard must not accept stale rows from fallback params.
"""

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_INSTALLED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _parse_start(provider: Any, row: dict[str, Any]) -> datetime | None:
    for key in (
        "date", "Date", "event_date", "start", "Start", "start_time", "startTime",
        "start_at", "startAt", "gameDate", "GameDate", "kickoff", "Kickoff",
    ):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            from app.utils import parse_datetime
            return parse_datetime(value).astimezone(UTC)
        except Exception:
            continue
    try:
        dt = provider._fixture_datetime(row)
        return dt.astimezone(UTC) if dt is not None else None
    except Exception:
        return None


def _filter_rows_for_date(provider: Any, rows: list[dict[str, Any]], date_key: str, slack_hours: int = 3) -> list[dict[str, Any]]:
    target = datetime.fromisoformat(date_key).replace(tzinfo=UTC)
    start_min = target - timedelta(hours=slack_hours)
    start_max = target + timedelta(days=1, hours=slack_hours)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _parse_start(provider, row)
        if start is None:
            continue
        if start_min <= start <= start_max:
            filtered.append(row)
    return filtered


def _team_obj(row: dict[str, Any], side: str) -> dict[str, Any]:
    value = row.get(f"{side}Team") or row.get(f"{side.capitalize()}Team")
    return value if isinstance(value, dict) else {}


def _team_id(row: dict[str, Any], side: str) -> str:
    obj = _team_obj(row, side)
    value = obj.get("id") or obj.get("Id") or row.get(f"{side}TeamId") or row.get(f"{side.capitalize()}TeamId")
    return str(value or "").strip()


def _team_name_from_row(row: dict[str, Any], side: str) -> str:
    obj = _team_obj(row, side)
    value = obj.get("name") or obj.get("Name") or row.get(f"{side}TeamName") or row.get(f"{side.capitalize()}TeamName") or row.get(f"{side}Team")
    if isinstance(value, dict):
        value = value.get("name") or value.get("Name")
    return str(value or "").strip()


def _patch_sstats_v1_goals_and_team_form() -> None:
    try:
        from app.providers.sstats_v1 import SStatsContextProvider
        from app.schemas import MatchContext
        from app.utils import canonicalize_team_name, clamp, score_event_match
    except Exception:
        return
    if getattr(SStatsContextProvider, "_harizon_result_field_and_team_id_fix", False):
        return

    @staticmethod
    def _goals(row: dict[str, Any], side: str) -> int | None:
        home_keys = [
            "homeGoals", "HomeGoals", "homeScore", "HomeScore", "homeResult", "HomeResult",
            "homeFTResult", "HomeFTResult", "homeFullTimeResult", "HomeFullTimeResult",
            "Score1", "score1", "home",
        ]
        away_keys = [
            "awayGoals", "AwayGoals", "awayScore", "AwayScore", "awayResult", "AwayResult",
            "awayFTResult", "AwayFTResult", "awayFullTimeResult", "AwayFullTimeResult",
            "Score2", "score2", "away",
        ]
        for key in (home_keys if side == "home" else away_keys):
            value = row.get(key)
            try:
                if value not in (None, "") and not isinstance(value, dict):
                    return int(float(value))
            except Exception:
                continue
        score = row.get("score") or row.get("Score")
        if isinstance(score, dict):
            keys = ("home", "Home", "homeResult", "homeFT") if side == "home" else ("away", "Away", "awayResult", "awayFT")
            for key in keys:
                value = score.get(key)
                try:
                    if value not in (None, "") and not isinstance(value, dict):
                        return int(float(value))
                except Exception:
                    continue
        return None

    def _league(row: dict[str, Any]) -> str:
        league = row.get("league") or row.get("League")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or "")
        season = row.get("season")
        if isinstance(season, dict):
            league_obj = season.get("league") or season.get("League")
            if isinstance(league_obj, dict):
                country = league_obj.get("country") if isinstance(league_obj.get("country"), dict) else {}
                country_name = str(country.get("name") or "").strip()
                league_name = str(league_obj.get("name") or league_obj.get("Name") or "").strip()
                return f"{country_name} - {league_name}".strip(" -")
        return str(row.get("leagueName") or row.get("LeagueName") or row.get("league") or "")

    def _key_variants_for_row(row: dict[str, Any], side: str) -> list[str]:
        out: list[str] = []
        tid = _team_id(row, side)
        if tid:
            out.append(f"id:{tid}")
        name = _team_name_from_row(row, side)
        canon = canonicalize_team_name(name)
        if canon:
            out.append(f"name:{canon}")
        raw = str(name or "").strip().lower()
        if raw:
            out.append(f"raw:{raw}")
        return list(dict.fromkeys(out))

    def _best_future_sstats_row(self: Any, match: Any, rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str | None]:
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_quality: str | None = None
        exact_tol = max(float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12), 12.0)
        fuzzy_tol = max(float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8), 30.0)
        start_min = match.commence_time.astimezone(UTC) - timedelta(hours=fuzzy_tol)
        start_max = match.commence_time.astimezone(UTC) + timedelta(hours=fuzzy_tol)
        for row in rows:
            start = self._start(row)
            if start is None or start < start_min or start > start_max:
                continue
            home = self._team(row, "home")
            away = self._team(row, "away")
            if not home or not away:
                continue
            league = _league(row)
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
                best, best_score, best_quality = row, score, quality
        if best_score < 58.0:
            return None, best_score, best_quality
        return best, best_score, best_quality

    def _team_form_contexts(self: Any, matches: list[Any], rows: list[dict[str, Any]], preview: dict[str, Any]) -> dict[str, MatchContext]:
        team_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        indexed_rows = 0
        for row in rows:
            start = self._start(row)
            home = self._team(row, "home")
            away = self._team(row, "away")
            hg = self._goals(row, "home")
            ag = self._goals(row, "away")
            if start is None or not home or not away or hg is None or ag is None:
                continue
            home_payload = {"start": start, "gf": float(hg), "ga": float(ag), "home": True, "team": home, "opponent": away, "row_id": self._game_id(row)}
            away_payload = {"start": start, "gf": float(ag), "ga": float(hg), "home": False, "team": away, "opponent": home, "row_id": self._game_id(row)}
            home_keys = _key_variants_for_row(row, "home")
            away_keys = _key_variants_for_row(row, "away")
            if not home_keys:
                hcanon = canonicalize_team_name(home)
                if hcanon:
                    home_keys = [f"name:{hcanon}"]
            if not away_keys:
                acanon = canonicalize_team_name(away)
                if acanon:
                    away_keys = [f"name:{acanon}"]
            for key in home_keys:
                team_rows[key].append(home_payload)
                indexed_rows += 1
            for key in away_keys:
                team_rows[key].append(away_payload)
                indexed_rows += 1
        for vals in team_rows.values():
            vals.sort(key=lambda x: x["start"], reverse=True)

        contexts: dict[str, MatchContext] = {}
        now = datetime.now(UTC)
        for match in matches:
            future_row, score, quality = _best_future_sstats_row(self, match, rows)
            home_keys = [f"name:{canonicalize_team_name(match.home_team)}"]
            away_keys = [f"name:{canonicalize_team_name(match.away_team)}"]
            if future_row is not None:
                home_keys = _key_variants_for_row(future_row, "home") + home_keys
                away_keys = _key_variants_for_row(future_row, "away") + away_keys
            home_keys = [k for k in dict.fromkeys(home_keys) if k and k != "name:"]
            away_keys = [k for k in dict.fromkeys(away_keys) if k and k != "name:"]

            home_rows: list[dict[str, Any]] = []
            away_rows: list[dict[str, Any]] = []
            home_key_used = ""
            away_key_used = ""
            for key in home_keys:
                values = team_rows.get(key, [])
                if len(values) > len(home_rows):
                    home_rows = values
                    home_key_used = key
            for key in away_keys:
                values = team_rows.get(key, [])
                if len(values) > len(away_rows):
                    away_rows = values
                    away_key_used = key
            home_rows = home_rows[: self.recent_limit]
            away_rows = away_rows[: self.recent_limit]
            if len(home_rows) < 3 or len(away_rows) < 3:
                if len(preview.setdefault("team_form_misses", [])) < 12:
                    preview["team_form_misses"].append({
                        "match_key": match.match_key,
                        "home": match.home_team,
                        "away": match.away_team,
                        "future_score": round(score, 2),
                        "future_quality": quality,
                        "home_keys": home_keys[:5],
                        "away_keys": away_keys[:5],
                        "home_sample": len(home_rows),
                        "away_sample": len(away_rows),
                    })
                continue
            h_for = sum(x["gf"] for x in home_rows) / len(home_rows)
            h_against = sum(x["ga"] for x in home_rows) / len(home_rows)
            a_for = sum(x["gf"] for x in away_rows) / len(away_rows)
            a_against = sum(x["ga"] for x in away_rows) / len(away_rows)
            expected_home = clamp((h_for + a_against) / 2.0, 0.25, 3.75)
            expected_away = clamp((a_for + h_against) / 2.0, 0.25, 3.75)
            recency_penalty = 0.0
            try:
                newest = max((home_rows[0]["start"], away_rows[0]["start"]))
                recency_penalty = min(4.0, max(0.0, (now - newest).total_seconds() / 86400.0) * 0.15)
            except Exception:
                recency_penalty = 0.0
            confidence = clamp(52.0 + min(len(home_rows), len(away_rows)) * 0.9 - recency_penalty, 52.0, 66.0)
            contexts[match.match_key] = MatchContext(
                source="sstats_form_v1",
                payload={"home_rows": home_rows, "away_rows": away_rows, "future_fixture": future_row},
                expected_home=round(expected_home, 3),
                expected_away=round(expected_away, 3),
                confidence=confidence,
                details={
                    "sstats_api_version": "v1",
                    "sstats_mode": "team_form_id_bridge",
                    "sstats_game_id": self._game_id(future_row or {}) if future_row else None,
                    "sstats_future_match_score": round(score, 2),
                    "sstats_future_match_quality": quality,
                    "home_recent_sample": len(home_rows),
                    "away_recent_sample": len(away_rows),
                    "home_key_used": home_key_used,
                    "away_key_used": away_key_used,
                    "home_gf_avg": round(h_for, 3),
                    "home_ga_avg": round(h_against, 3),
                    "away_gf_avg": round(a_for, 3),
                    "away_ga_avg": round(a_against, 3),
                },
            )
            if len(preview.setdefault("team_form_examples", [])) < 12:
                preview["team_form_examples"].append({
                    "match_key": match.match_key,
                    "expected_home": round(expected_home, 3),
                    "expected_away": round(expected_away, 3),
                    "home_sample": len(home_rows),
                    "away_sample": len(away_rows),
                    "home_key_used": home_key_used,
                    "away_key_used": away_key_used,
                    "future_score": round(score, 2),
                    "quality": quality,
                })
        preview["team_form_index_keys"] = len(team_rows)
        preview["team_form_indexed_rows"] = indexed_rows
        return contexts

    SStatsContextProvider._goals = _goals
    SStatsContextProvider._league = staticmethod(_league)
    SStatsContextProvider._team_form_contexts = _team_form_contexts
    SStatsContextProvider._harizon_result_field_and_team_id_fix = True


def _patch_sportlogic_query_guard() -> None:
    try:
        import app.services.sportlogic_query_runtime_guard as guard
    except Exception:
        return
    if getattr(guard, "_HARIZON_STALE_FILTER_FIX", False):
        return

    def _param_variants_for_date(date_key: str, per_page: int = 100) -> list[dict[str, Any]]:
        return [
            {"date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"status": "scheduled", "date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"status": "pending", "date_from": date_key, "date_to": date_key, "per_page": per_page},
            {"starts_at_from": f"{date_key}T00:00:00Z", "starts_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
            {"start_at_from": f"{date_key}T00:00:00Z", "start_at_to": f"{date_key}T23:59:59Z", "per_page": per_page},
            {"date": date_key, "per_page": per_page},
            {"from": date_key, "to": date_key, "per_page": per_page},
        ]

    def _broad_param_variants(per_page: int = 100) -> list[dict[str, Any]]:
        return [
            {"status": "scheduled", "per_page": per_page},
            {"status": "pending", "per_page": per_page},
            {"sport": "football", "per_page": per_page},
            {"sport": "soccer", "per_page": per_page},
            {"per_page": per_page},
        ]

    async def _load_fixtures_with_fallback(provider: Any, dates: list[str], stats: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
        import httpx

        fixtures: list[dict[str, Any]] = []
        per_page = max(5, int(float(os.getenv("SPORTLOGIC_SMOKE_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE") or 100)))
        async with httpx.AsyncClient(timeout=provider.timeout, follow_redirects=True) as client:
            for date_key in dates:
                if not provider._budget_left():
                    stats["budget_exhausted"] = True
                    break
                day_rows: list[dict[str, Any]] = []
                for params in _param_variants_for_date(date_key, per_page=per_page):
                    if not provider._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await provider._get_json(client, "/games", params, stats, preview)
                    raw_rows = provider._extract_list(payload)
                    rows = _filter_rows_for_date(provider, raw_rows, date_key)
                    if raw_rows and not rows:
                        preview.setdefault("stale_query_variants_rejected", []).append({
                            "scope": "dated",
                            "date": date_key,
                            "params": params,
                            "raw_rows": len(raw_rows),
                            "kept_rows": 0,
                        })
                        continue
                    if rows:
                        day_rows.extend(rows)
                        preview.setdefault("query_variants_used", []).append({
                            "scope": "dated",
                            "date": date_key,
                            "params": params,
                            "rows": len(rows),
                            "raw_rows": len(raw_rows),
                        })
                        break
                fixtures.extend(day_rows)
            if not fixtures and _truthy(os.getenv("SPORTLOGIC_BROAD_FALLBACK_ENABLED"), False):
                today = datetime.now(UTC).date().isoformat()
                for params in _broad_param_variants(per_page=per_page):
                    if not provider._budget_left():
                        stats["budget_exhausted"] = True
                        break
                    payload = await provider._get_json(client, "/games", params, stats, preview)
                    raw_rows = provider._extract_list(payload)
                    rows: list[dict[str, Any]] = []
                    for date_key in dates or [today]:
                        rows.extend(_filter_rows_for_date(provider, raw_rows, date_key, slack_hours=6))
                    if rows:
                        fixtures.extend(rows)
                        preview.setdefault("query_variants_used", []).append({"scope": "broad_filtered", "date": "broad", "params": params, "rows": len(rows), "raw_rows": len(raw_rows)})
                        break
                    if raw_rows:
                        preview.setdefault("stale_query_variants_rejected", []).append({"scope": "broad", "date": "broad", "params": params, "raw_rows": len(raw_rows), "kept_rows": 0})
        return guard._dedupe_rows(fixtures, provider)

    guard._param_variants_for_date = _param_variants_for_date
    guard._broad_param_variants = _broad_param_variants
    guard._load_fixtures_with_fallback = _load_fixtures_with_fallback
    guard._HARIZON_STALE_FILTER_FIX = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    _patch_sstats_v1_goals_and_team_form()
    _patch_sportlogic_query_guard()
    return {"status": "installed", "sstats_result_fields": True, "sstats_team_id_form_bridge": True, "sportlogic_stale_filter": True}
