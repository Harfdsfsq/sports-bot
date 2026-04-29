from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, parse_datetime, team_similarity

UTC = timezone.utc


class ExternalSignalsContextProvider:
    """Low-cost external context/signal layer.

    This provider does not publish picks and does not create odds-derived value by
    itself. It enriches matches with independent signals from cheap/free sources:
    ClubElo, Football-Data.co.uk, Open-Meteo, NewsData.io, Guardian, Wikidata and
    optional Highlightly. Signals are intentionally low-confidence unless several
    independent sources agree.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.enabled = self._env_bool("ENABLE_EXTERNAL_SIGNALS", True)
        self.max_http_requests = self._env_int("EXTERNAL_SIGNALS_PER_RUN_MAX", 40)
        self.context_match_limit = self._env_int("EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT", 80)
        self.timeout = float(os.getenv("EXTERNAL_SIGNALS_TIMEOUT_SECONDS", "12") or 12)
        self.cache_root = Path(os.getenv("EXTERNAL_SIGNALS_CACHE_DIR", ".data/provider_cache/external_signals"))
        self.requests = 0

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.enabled,
            "requests": 0,
            "budget_exhausted": False,
            "contexts_built": 0,
            "clubelo_contexts": 0,
            "football_data_uk_contexts": 0,
            "open_meteo_contexts": 0,
            "newsdata_contexts": 0,
            "guardian_contexts": 0,
            "wikidata_contexts": 0,
            "highlightly_contexts": 0,
            "response_errors": 0,
            "max_http_requests_per_run": self.max_http_requests,
        }
        preview: dict[str, Any] = {"sample_contexts": [], "errors": []}
        if not self.enabled:
            return {}, stats, preview
        soccer_matches = [m for m in matches if m.sport_key == "soccer"][: max(0, self.context_match_limit)]
        if not soccer_matches:
            return {}, stats, preview

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            football_data_uk = await self._football_data_uk_team_index(client, stats, preview)
            contexts: dict[str, MatchContext] = {}
            for match in soccer_matches:
                signals: dict[str, Any] = {}
                clubelo = await self._clubelo_match_signal(client, match, stats, preview)
                if clubelo:
                    signals["clubelo"] = clubelo
                    stats["clubelo_contexts"] += 1
                fduk = self._football_data_uk_signal(match, football_data_uk)
                if fduk:
                    signals["football_data_co_uk"] = fduk
                    stats["football_data_uk_contexts"] += 1
                meteo = await self._open_meteo_signal(client, match, stats, preview)
                if meteo:
                    signals["open_meteo"] = meteo
                    stats["open_meteo_contexts"] += 1
                newsdata = await self._newsdata_signal(client, match, stats, preview)
                if newsdata:
                    signals["newsdata"] = newsdata
                    stats["newsdata_contexts"] += 1
                guardian = await self._guardian_signal(client, match, stats, preview)
                if guardian:
                    signals["guardian"] = guardian
                    stats["guardian_contexts"] += 1
                wikidata = await self._wikidata_signal(client, match, stats, preview)
                if wikidata:
                    signals["wikidata"] = wikidata
                    stats["wikidata_contexts"] += 1
                highlightly = await self._highlightly_signal(client, match, stats, preview)
                if highlightly:
                    signals["highlightly"] = highlightly
                    stats["highlightly_contexts"] += 1

                context = self._build_context(match, signals)
                if context is None:
                    continue
                contexts[match.match_key] = context
                if len(preview["sample_contexts"]) < 8:
                    preview["sample_contexts"].append({
                        "match_key": match.match_key,
                        "home": match.home_team,
                        "away": match.away_team,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                        "confidence": context.confidence,
                        "signals": sorted(signals.keys()),
                    })

        stats["requests"] = self.requests
        stats["budget_exhausted"] = self.requests >= self.max_http_requests > 0
        stats["contexts_built"] = len(contexts)
        return contexts, stats, preview

    def _build_context(self, match: Match, signals: dict[str, Any]) -> MatchContext | None:
        if not signals:
            return None
        elo = signals.get("clubelo") or {}
        fduk = signals.get("football_data_co_uk") or {}
        highlightly = signals.get("highlightly") or {}

        # Low-confidence xG proxy. It should support existing context, not replace strong APIs.
        total_goal_estimates: list[tuple[float, float]] = []
        if fduk.get("home_goals_for") is not None and fduk.get("away_goals_for") is not None:
            home_base = (float(fduk["home_goals_for"]) + float(fduk.get("away_goals_against") or fduk["home_goals_for"])) / 2.0
            away_base = (float(fduk["away_goals_for"]) + float(fduk.get("home_goals_against") or fduk["away_goals_for"])) / 2.0
            total_goal_estimates.append((clamp(home_base, 0.35, 2.8), clamp(away_base, 0.35, 2.8)))
        if elo.get("elo_diff") is not None:
            diff = float(elo["elo_diff"])
            base_total = 2.55
            home_share = clamp(0.52 + diff / 1200.0, 0.34, 0.70)
            total_goal_estimates.append((base_total * home_share, base_total * (1.0 - home_share)))
        if highlightly.get("expected_home") is not None and highlightly.get("expected_away") is not None:
            total_goal_estimates.append((float(highlightly["expected_home"]), float(highlightly["expected_away"])))

        expected_home = expected_away = None
        if total_goal_estimates:
            expected_home = round(sum(item[0] for item in total_goal_estimates) / len(total_goal_estimates), 3)
            expected_away = round(sum(item[1] for item in total_goal_estimates) / len(total_goal_estimates), 3)

        home_win_probability = None
        away_win_probability = None
        if elo.get("elo_diff") is not None:
            diff = float(elo["elo_diff"])
            p_home_no_draw = 1.0 / (1.0 + math.exp(-diff / 260.0))
            draw = 0.25
            home_win_probability = clamp((1.0 - draw) * p_home_no_draw, 0.05, 0.82)
            away_win_probability = clamp((1.0 - draw) * (1.0 - p_home_no_draw), 0.05, 0.82)

        source_count = len(signals)
        confidence = 50.0 + min(8.0, source_count * 1.6)
        if expected_home is not None and expected_away is not None:
            confidence += 2.0
        if "football_data_co_uk" in signals and "clubelo" in signals:
            confidence += 2.0
        if "newsdata" in signals or "guardian" in signals:
            confidence += 0.5
        confidence = clamp(confidence, 50.0, 63.0)

        return MatchContext(
            source="external_signals",
            payload={"signals": signals},
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=home_win_probability,
            away_win_probability=away_win_probability,
            confidence=confidence,
            details={
                "external_signal_sources": sorted(signals.keys()),
                "signal_count": source_count,
                "note": "Low-confidence independent signal layer; used as confirmation, not standalone publication proof.",
            },
        )

    async def _clubelo_match_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        if not self._env_bool("ENABLE_CLUBELO_CONTEXT", True):
            return None
        home = await self._clubelo_team(client, match.home_team, stats, preview)
        away = await self._clubelo_team(client, match.away_team, stats, preview)
        if not home and not away:
            return None
        return {
            "home_elo": home.get("elo") if home else None,
            "away_elo": away.get("elo") if away else None,
            "elo_diff": (float(home["elo"]) - float(away["elo"])) if home and away and home.get("elo") is not None and away.get("elo") is not None else None,
            "home_rank": home.get("rank") if home else None,
            "away_rank": away.get("rank") if away else None,
        }

    async def _clubelo_team(self, client: httpx.AsyncClient, team: str, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        key = re.sub(r"[^a-z0-9]+", "", canonicalize_team_name(team).lower())
        if not key:
            return None
        cached = self._cache_get("clubelo", key, ttl_hours=self._env_int("CLUBELO_CACHE_TTL_HOURS", 72))
        if isinstance(cached, dict):
            return cached or None
        if not self._budget_left():
            return None
        url = f"http://api.clubelo.com/{quote(team)}"
        try:
            self.requests += 1
            response = await client.get(url)
            if response.status_code != 200 or not response.text.strip():
                self._cache_put("clubelo", key, {})
                return None
            rows = list(csv.DictReader(io.StringIO(response.text)))
            if not rows:
                self._cache_put("clubelo", key, {})
                return None
            row = rows[-1]
            payload = {
                "team": row.get("Club") or team,
                "elo": self._float(row.get("Elo")),
                "rank": self._int(row.get("Rank")),
                "from": row.get("From"),
                "to": row.get("To"),
            }
            self._cache_put("clubelo", key, payload)
            return payload
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "clubelo", exc)
            return None

    async def _football_data_uk_team_index(self, client: httpx.AsyncClient, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if not self._env_bool("ENABLE_FOOTBALL_DATA_UK_CONTEXT", True):
            return {}
        season = os.getenv("FOOTBALL_DATA_UK_SEASON") or self._current_football_data_uk_season()
        codes = [item.strip() for item in os.getenv("FOOTBALL_DATA_UK_LEAGUE_CODES", "E0,E1,D1,I1,SP1,F1,N1,P1,B1,SC0,T1").split(",") if item.strip()]
        cache_key = f"{season}_{'_'.join(codes)}"
        cached = self._cache_get("football_data_uk", cache_key, ttl_hours=self._env_int("FOOTBALL_DATA_UK_CACHE_TTL_HOURS", 12))
        if isinstance(cached, dict):
            return cached
        index: dict[str, dict[str, Any]] = {}
        for code in codes[: self._env_int("FOOTBALL_DATA_UK_MAX_LEAGUES_PER_RUN", 12)]:
            if not self._budget_left():
                break
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            try:
                self.requests += 1
                response = await client.get(url)
                if response.status_code != 200 or not response.text.strip():
                    continue
                for row in csv.DictReader(io.StringIO(response.text)):
                    home = str(row.get("HomeTeam") or "").strip()
                    away = str(row.get("AwayTeam") or "").strip()
                    if not home or not away:
                        continue
                    hg = self._float(row.get("FTHG"))
                    ag = self._float(row.get("FTAG"))
                    if hg is None or ag is None:
                        continue
                    self._add_team_result(index, home, gf=hg, ga=ag, home=True, league_code=code)
                    self._add_team_result(index, away, gf=ag, ga=hg, home=False, league_code=code)
            except Exception as exc:
                stats["response_errors"] += 1
                self._preview_error(preview, "football_data_uk", exc)
        summarized = {team: self._summarize_team_results(rows) for team, rows in index.items()}
        self._cache_put("football_data_uk", cache_key, summarized)
        return summarized

    def _football_data_uk_signal(self, match: Match, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        home = self._best_team_index_match(match.home_team, index)
        away = self._best_team_index_match(match.away_team, index)
        if not home and not away:
            return None
        return {
            "home_match_name": home[0] if home else None,
            "away_match_name": away[0] if away else None,
            "home_goals_for": (home[1] or {}).get("goals_for") if home else None,
            "home_goals_against": (home[1] or {}).get("goals_against") if home else None,
            "away_goals_for": (away[1] or {}).get("goals_for") if away else None,
            "away_goals_against": (away[1] or {}).get("goals_against") if away else None,
            "home_sample": (home[1] or {}).get("sample") if home else 0,
            "away_sample": (away[1] or {}).get("sample") if away else 0,
        }

    async def _open_meteo_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        if not self._env_bool("ENABLE_OPEN_METEO_CONTEXT", True):
            return None
        meta = match.metadata or {}
        lat = self._float(meta.get("latitude") or meta.get("lat") or meta.get("venue_latitude"))
        lon = self._float(meta.get("longitude") or meta.get("lon") or meta.get("venue_longitude"))
        if lat is None or lon is None:
            return None
        cache_key = f"{round(lat, 3)}_{round(lon, 3)}_{match.commence_time.date().isoformat()}"
        cached = self._cache_get("open_meteo", cache_key, ttl_hours=self._env_int("OPEN_METEO_CACHE_TTL_HOURS", 6))
        if isinstance(cached, dict):
            return cached or None
        if not self._budget_left():
            return None
        try:
            self.requests += 1
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m",
                    "timezone": "UTC",
                    "forecast_days": 3,
                },
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            signal = self._nearest_open_meteo_hour(payload, match.commence_time)
            self._cache_put("open_meteo", cache_key, signal or {})
            return signal
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "open_meteo", exc)
            return None

    async def _newsdata_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        api_key = os.getenv("NEWSDATA_API_KEY") or os.getenv("NEWSDATA_IO_KEY")
        if not api_key or not self._env_bool("ENABLE_NEWSDATA_CONTEXT", True):
            return None
        if not self._budget_left():
            return None
        query = f'"{match.home_team}" OR "{match.away_team}" football'
        try:
            self.requests += 1
            response = await client.get("https://newsdata.io/api/1/news", params={"apikey": api_key, "q": query, "language": "en", "size": 5})
            if response.status_code != 200:
                return None
            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else []
            if not isinstance(results, list) or not results:
                return None
            return {"article_count": len(results), "titles": [str((item or {}).get("title") or "")[:120] for item in results[:3] if isinstance(item, dict)]}
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "newsdata", exc)
            return None

    async def _guardian_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        api_key = os.getenv("GUARDIAN_API_KEY") or os.getenv("GUARDIAN_OPEN_PLATFORM_KEY")
        if not api_key or not self._env_bool("ENABLE_GUARDIAN_CONTEXT", True):
            return None
        if not self._budget_left():
            return None
        query = f'"{match.home_team}" OR "{match.away_team}" football'
        try:
            self.requests += 1
            response = await client.get("https://content.guardianapis.com/search", params={"api-key": api_key, "q": query, "page-size": 5, "section": "football"})
            if response.status_code != 200:
                return None
            payload = response.json()
            results = ((payload.get("response") or {}).get("results") if isinstance(payload, dict) else []) or []
            if not isinstance(results, list) or not results:
                return None
            return {"article_count": len(results), "titles": [str((item or {}).get("webTitle") or "")[:120] for item in results[:3] if isinstance(item, dict)]}
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "guardian", exc)
            return None

    async def _wikidata_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        if not self._env_bool("ENABLE_WIKIDATA_CONTEXT", True):
            return None
        # Cheap, cacheable metadata check. Avoids heavy SPARQL on every run.
        found: dict[str, Any] = {}
        for side, team in (("home", match.home_team), ("away", match.away_team)):
            key = re.sub(r"[^a-z0-9]+", "_", canonicalize_team_name(team).lower()).strip("_")
            cached = self._cache_get("wikidata", key, ttl_hours=self._env_int("WIKIDATA_CACHE_TTL_HOURS", 168))
            if isinstance(cached, dict):
                if cached:
                    found[side] = cached
                continue
            if not self._budget_left():
                break
            try:
                self.requests += 1
                response = await client.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities", "search": team, "language": "en", "format": "json", "limit": 1})
                if response.status_code != 200:
                    continue
                payload = response.json()
                rows = payload.get("search") if isinstance(payload, dict) else []
                item = rows[0] if isinstance(rows, list) and rows else {}
                data = {"id": item.get("id"), "label": item.get("label"), "description": item.get("description")} if isinstance(item, dict) and item.get("id") else {}
                self._cache_put("wikidata", key, data)
                if data:
                    found[side] = data
            except Exception as exc:
                stats["response_errors"] += 1
                self._preview_error(preview, "wikidata", exc)
        return found or None

    async def _highlightly_signal(self, client: httpx.AsyncClient, match: Match, stats: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
        api_key = os.getenv("HIGHLIGHTLY_API_KEY") or os.getenv("HIGHLIGHTLY_RAPIDAPI_KEY")
        if not api_key or not self._env_bool("ENABLE_HIGHLIGHTLY_CONTEXT", True):
            return None
        base = (os.getenv("HIGHLIGHTLY_BASE_URL") or "https://highlightly.net/api").rstrip("/")
        path = os.getenv("HIGHLIGHTLY_FIXTURES_PATH") or "/football/matches"
        if not self._budget_left():
            return None
        headers = {"Authorization": f"Bearer {api_key}"}
        if os.getenv("HIGHLIGHTLY_RAPIDAPI_HOST"):
            headers = {"x-rapidapi-key": api_key, "x-rapidapi-host": os.getenv("HIGHLIGHTLY_RAPIDAPI_HOST", "")}
        try:
            self.requests += 1
            response = await client.get(f"{base}{path}", headers=headers, params={"date": match.commence_time.date().isoformat()})
            if response.status_code != 200:
                return None
            payload = response.json()
            rows = self._extract_list(payload)
            best: dict[str, Any] | None = None
            best_score = 0.0
            for row in rows:
                home = str(row.get("homeTeam") or row.get("home_team") or row.get("home") or "")
                away = str(row.get("awayTeam") or row.get("away_team") or row.get("away") or "")
                score = min(team_similarity(match.home_team, home), team_similarity(match.away_team, away))
                if score > best_score:
                    best_score = score
                    best = row
            if not best or best_score < 0.58:
                return None
            return {
                "match_score": round(best_score, 3),
                "expected_home": self._float(best.get("expected_home") or best.get("home_xg")),
                "expected_away": self._float(best.get("expected_away") or best.get("away_xg")),
                "status": best.get("status"),
            }
        except Exception as exc:
            stats["response_errors"] += 1
            self._preview_error(preview, "highlightly", exc)
            return None

    def _budget_left(self) -> bool:
        return self.max_http_requests <= 0 or self.requests < self.max_http_requests

    def _cache_get(self, group: str, key: str, ttl_hours: int) -> Any | None:
        path = self.cache_root / group / f"{key}.json"
        try:
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            created = parse_datetime(payload.get("created_at"))
            if datetime.now(UTC) - created > timedelta(hours=max(1, ttl_hours)):
                return None
            return payload.get("data")
        except Exception:
            return None

    def _cache_put(self, group: str, key: str, data: Any) -> None:
        path = self.cache_root / group / f"{key}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"created_at": datetime.now(UTC).isoformat(), "data": data}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or raw == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(float(os.getenv(name, str(default))))
        except Exception:
            return default

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(float(value))
        except Exception:
            return None

    @staticmethod
    def _current_football_data_uk_season() -> str:
        now = datetime.now(UTC)
        start_year = now.year - 1 if now.month < 7 else now.year
        return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"

    @staticmethod
    def _add_team_result(index: dict[str, list[dict[str, Any]]], team: str, *, gf: float, ga: float, home: bool, league_code: str) -> None:
        key = canonicalize_team_name(team)
        if not key:
            return
        index.setdefault(key, []).append({"goals_for": gf, "goals_against": ga, "home": home, "league_code": league_code})

    @staticmethod
    def _summarize_team_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
        sample = rows[-8:] if len(rows) > 8 else rows
        if not sample:
            return {"sample": 0}
        return {
            "sample": len(sample),
            "goals_for": round(sum(float(x["goals_for"]) for x in sample) / len(sample), 3),
            "goals_against": round(sum(float(x["goals_against"]) for x in sample) / len(sample), 3),
            "league_codes": sorted({str(x.get("league_code") or "") for x in sample if x.get("league_code")}),
        }

    @staticmethod
    def _best_team_index_match(team: str, index: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
        best_name = ""
        best_score = 0.0
        for name in index:
            score = team_similarity(team, name)
            if score > best_score:
                best_score = score
                best_name = name
        if not best_name or best_score < 0.66:
            return None
        return best_name, index[best_name]

    @staticmethod
    def _nearest_open_meteo_hour(payload: dict[str, Any], commence_time: datetime) -> dict[str, Any] | None:
        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        if not isinstance(hourly, dict):
            return None
        times = hourly.get("time") or []
        if not isinstance(times, list) or not times:
            return None
        target = commence_time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        best_idx = None
        best_diff = 999999.0
        for idx, raw in enumerate(times):
            try:
                dt = parse_datetime(str(raw).replace("T", " "))
            except Exception:
                try:
                    dt = datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
                except Exception:
                    continue
            diff = abs((dt - target).total_seconds())
            if diff < best_diff:
                best_idx = idx
                best_diff = diff
        if best_idx is None:
            return None
        def at(key: str) -> Any:
            arr = hourly.get(key) or []
            return arr[best_idx] if isinstance(arr, list) and best_idx < len(arr) else None
        return {
            "temperature_c": at("temperature_2m"),
            "precipitation_mm": at("precipitation"),
            "wind_speed_kmh": at("wind_speed_10m"),
            "wind_gusts_kmh": at("wind_gusts_10m"),
        }

    @staticmethod
    def _extract_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "results", "matches", "fixtures"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [x for x in rows if isinstance(x, dict)]
        return []

    @staticmethod
    def _preview_error(preview: dict[str, Any], source: str, exc: Exception) -> None:
        errors = preview.setdefault("errors", [])
        if isinstance(errors, list) and len(errors) < 8:
            errors.append({"source": source, "error": f"{exc.__class__.__name__}: {exc}"})
