from __future__ import annotations

import csv
import io
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, team_similarity

UTC = timezone.utc


CLUBELO_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "manchester united": ("ManUnited", "ManUtd"),
    "manchester city": ("ManCity", "ManchesterCity"),
    "tottenham hotspur": ("Tottenham", "Spurs"),
    "wolverhampton wanderers": ("Wolves", "Wolverhampton"),
    "brighton and hove albion": ("Brighton",),
    "nottingham forest": ("NottmForest", "NottinghamForest"),
    "newcastle united": ("Newcastle",),
    "west ham united": ("WestHam",),
    "leeds united": ("Leeds",),
    "leicester city": ("Leicester",),
    "sheffield united": ("SheffieldUnited", "SheffieldUtd"),
    "real madrid": ("RealMadrid",),
    "atletico madrid": ("Atletico", "AtleticoMadrid"),
    "athletic bilbao": ("Bilbao", "Athletic"),
    "real sociedad": ("Sociedad", "RealSociedad"),
    "real betis": ("Betis", "RealBetis"),
    "villarreal": ("Villarreal",),
    "barcelona": ("Barcelona", "Barca"),
    "sevilla": ("Sevilla",),
    "internazionale": ("Inter", "Internazionale"),
    "inter milan": ("Inter", "Internazionale"),
    "ac milan": ("Milan",),
    "milan": ("Milan",),
    "roma": ("Roma",),
    "lazio": ("Lazio",),
    "napoli": ("Napoli",),
    "juventus": ("Juventus",),
    "atalanta": ("Atalanta",),
    "bayern munich": ("Bayern", "BayernMunich"),
    "bayern munchen": ("Bayern", "BayernMunich"),
    "borussia dortmund": ("Dortmund",),
    "bayer leverkusen": ("Leverkusen",),
    "rb leipzig": ("RBLeipzig", "Leipzig"),
    "eintracht frankfurt": ("Frankfurt",),
    "borussia monchengladbach": ("Gladbach",),
    "paris saint germain": ("PSG", "ParisSG"),
    "psg": ("PSG", "ParisSG"),
    "olympique marseille": ("Marseille",),
    "olympique lyon": ("Lyon",),
    "sporting cp": ("Sporting", "SportingCP"),
    "sporting lisbon": ("Sporting", "SportingCP"),
    "fc porto": ("Porto",),
    "benfica": ("Benfica",),
    "ajax": ("Ajax",),
    "psv eindhoven": ("PSV",),
    "feyenoord": ("Feyenoord",),
    "club brugge": ("ClubBrugge",),
    "celtic": ("Celtic",),
    "rangers": ("Rangers",),
}


class ClubEloContextProvider:
    """Free ClubElo context adapter.

    ClubElo is used as a low-cost strength prior. The provider first tries the
    date snapshot endpoint (`/<YYYY-MM-DD>`) and falls back to per-club CSV
    histories when the snapshot is unavailable or a club was not resolved.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(os.getenv("CLUBELO_BASE_URL") or getattr(settings, "clubelo_base_url", None) or "http://api.clubelo.com").rstrip("/")
        self.timeout = float(os.getenv("CLUBELO_TIMEOUT_SECONDS") or getattr(settings, "clubelo_timeout_seconds", None) or 18.0)
        self.max_http_requests = max(0, int(float(os.getenv("CLUBELO_REQUESTS_MAX_PER_RUN") or getattr(settings, "clubelo_requests_max_per_run", None) or 16)))
        self.match_limit = max(1, int(float(os.getenv("CLUBELO_CONTEXT_MATCH_LIMIT") or getattr(settings, "clubelo_context_match_limit", None) or 180)))
        self.enabled = self._env_bool("ENABLE_CLUBELO_CONTEXT", True) and self._env_bool("CLUBELO_ENABLED", True)
        self._requests = 0
        self._snapshot_cache: dict[str, list[dict[str, str]]] = {}
        self._team_cache: dict[str, list[dict[str, str]]] = {}

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.enabled,
            "requests": 0,
            "response_errors": 0,
            "http_statuses": [],
            "target_matches": 0,
            "snapshot_requests": 0,
            "team_history_requests": 0,
            "snapshot_rows": 0,
            "team_rows": 0,
            "ratings_resolved": 0,
            "contexts_built": 0,
            "missing_ratings": 0,
            "budget_exhausted": False,
            "last_body_preview": None,
            "max_http_requests_per_run": self.max_http_requests,
        }
        preview: dict[str, Any] = {"sample_ratings": [], "sample_contexts": [], "missing_examples": []}
        if not self.enabled:
            return {}, stats, preview
        if self.max_http_requests <= 0:
            stats["budget_exhausted"] = True
            return {}, stats, preview

        soccer_matches = [item for item in matches if getattr(item, "sport_key", "") == "soccer"][: self.match_limit]
        stats["target_matches"] = len(soccer_matches)
        if not soccer_matches:
            return {}, stats, preview

        contexts: dict[str, MatchContext] = {}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for match in soccer_matches:
                home_rating = await self._rating_for_match_team(client, match.home_team, match.commence_time, stats)
                away_rating = await self._rating_for_match_team(client, match.away_team, match.commence_time, stats)
                if home_rating is None or away_rating is None:
                    stats["missing_ratings"] += 1
                    if len(preview["missing_examples"]) < 8:
                        preview["missing_examples"].append({
                            "match_key": match.match_key,
                            "home_team": match.home_team,
                            "away_team": match.away_team,
                            "home_rating_found": home_rating is not None,
                            "away_rating_found": away_rating is not None,
                        })
                    continue
                stats["ratings_resolved"] += 2
                context = self._ratings_to_context(match, home_rating, away_rating)
                contexts[match.match_key] = context
                stats["contexts_built"] += 1
                if len(preview["sample_ratings"]) < 8:
                    preview["sample_ratings"].append({
                        "team": match.home_team,
                        "clubelo_name": home_rating.get("Club"),
                        "elo": home_rating.get("Elo"),
                    })
                    preview["sample_ratings"].append({
                        "team": match.away_team,
                        "clubelo_name": away_rating.get("Club"),
                        "elo": away_rating.get("Elo"),
                    })
                if len(preview["sample_contexts"]) < 8:
                    preview["sample_contexts"].append({
                        "match_key": match.match_key,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                        "confidence": context.confidence,
                    })

        return contexts, stats, preview

    def supports_match(self, match: Match) -> bool:
        if getattr(match, "sport_key", "") != "soccer":
            return False
        league = str(getattr(match, "league_name", "") or "").lower()
        return not any(token in league for token in ("women", "youth", "u19", "u20", "u21", "u23", "reserve", "friendly"))

    async def _rating_for_match_team(self, client: httpx.AsyncClient, team_name: str, commence_time: datetime, stats: dict[str, Any]) -> dict[str, str] | None:
        date_key = commence_time.astimezone(UTC).date().isoformat()
        snapshot = await self._snapshot(client, date_key, stats)
        row = self._find_rating_row(team_name, snapshot)
        if row is not None:
            return row

        prev_date = (commence_time.astimezone(UTC).date() - timedelta(days=1)).isoformat()
        if prev_date != date_key:
            snapshot = await self._snapshot(client, prev_date, stats)
            row = self._find_rating_row(team_name, snapshot)
            if row is not None:
                return row

        for slug in self._team_slug_candidates(team_name)[:3]:
            history = await self._team_history(client, slug, stats)
            if not history:
                continue
            row = self._latest_history_row(history, commence_time)
            if row is not None:
                return row
        return None

    async def _snapshot(self, client: httpx.AsyncClient, date_key: str, stats: dict[str, Any]) -> list[dict[str, str]]:
        if date_key in self._snapshot_cache:
            return self._snapshot_cache[date_key]
        payload = await self._fetch_text(client, f"/{date_key}", stats)
        stats["snapshot_requests"] += 1
        rows = self._parse_csv(payload)
        stats["snapshot_rows"] += len(rows)
        self._snapshot_cache[date_key] = rows
        return rows

    async def _team_history(self, client: httpx.AsyncClient, slug: str, stats: dict[str, Any]) -> list[dict[str, str]]:
        if slug in self._team_cache:
            return self._team_cache[slug]
        payload = await self._fetch_text(client, f"/{slug}", stats)
        stats["team_history_requests"] += 1
        rows = self._parse_csv(payload)
        stats["team_rows"] += len(rows)
        self._team_cache[slug] = rows
        return rows

    async def _fetch_text(self, client: httpx.AsyncClient, path: str, stats: dict[str, Any]) -> str:
        if self.max_http_requests <= 0 or self._requests >= self.max_http_requests:
            stats["budget_exhausted"] = True
            return ""
        self._requests += 1
        stats["requests"] += 1
        try:
            response = await client.get(f"{self.base_url}{path}")
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_body_preview"] = f"request failed: {exc}"
            return ""
        stats["http_statuses"].append(response.status_code)
        stats["last_body_preview"] = response.text[:1000]
        if response.status_code >= 400:
            stats["response_errors"] += 1
            return ""
        return response.text

    @staticmethod
    def _parse_csv(payload: str) -> list[dict[str, str]]:
        text = str(payload or "").strip()
        if not text or "," not in text:
            return []
        try:
            return [dict(row) for row in csv.DictReader(io.StringIO(text)) if isinstance(row, dict)]
        except Exception:
            return []

    def _find_rating_row(self, team_name: str, rows: list[dict[str, str]]) -> dict[str, str] | None:
        if not rows:
            return None
        target = canonicalize_team_name(team_name)
        best_row: dict[str, str] | None = None
        best_score = 0.0
        for row in rows:
            club = str(row.get("Club") or row.get("club") or "").strip()
            if not club:
                continue
            score = team_similarity(target, club)
            if canonicalize_team_name(club) == target:
                score = 1.0
            if score > best_score:
                best_score = score
                best_row = row
        threshold = float(os.getenv("CLUBELO_TEAM_MATCH_THRESHOLD") or getattr(self.settings, "clubelo_team_match_threshold", None) or 0.58)
        return best_row if best_row is not None and best_score >= threshold else None

    @staticmethod
    def _latest_history_row(rows: list[dict[str, str]], commence_time: datetime) -> dict[str, str] | None:
        match_date = commence_time.astimezone(UTC).date()
        best: tuple[datetime, dict[str, str]] | None = None
        for row in rows:
            raw = str(row.get("From") or row.get("To") or row.get("Date") or "")[:10]
            try:
                dt = datetime.fromisoformat(raw).replace(tzinfo=UTC)
            except Exception:
                continue
            if dt.date() > match_date:
                continue
            if best is None or dt > best[0]:
                best = (dt, row)
        return best[1] if best else (rows[-1] if rows else None)

    def _team_slug_candidates(self, team_name: str) -> list[str]:
        key = canonicalize_team_name(team_name)
        candidates: list[str] = []

        def add(value: str) -> None:
            cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(value or ""))
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        for alias in CLUBELO_TEAM_ALIASES.get(key, ()):
            add(alias)
        add("".join(part[:1].upper() + part[1:] for part in key.split()))
        tokens = [token for token in key.split() if token not in {"fc", "cf", "sc", "club"}]
        if len(tokens) >= 2:
            add("".join(part[:1].upper() + part[1:] for part in tokens))
            add(tokens[-1].title())
        add(team_name)
        return candidates

    def _ratings_to_context(self, match: Match, home_row: dict[str, str], away_row: dict[str, str]) -> MatchContext:
        home_elo = self._float(home_row.get("Elo") or home_row.get("elo")) or 1500.0
        away_elo = self._float(away_row.get("Elo") or away_row.get("elo")) or 1500.0
        home_advantage = float(os.getenv("CLUBELO_HOME_ADVANTAGE") or getattr(self.settings, "clubelo_home_advantage", None) or 55.0)
        elo_diff = (home_elo + home_advantage) - away_elo
        raw_home = 1.0 / (1.0 + math.pow(10.0, -elo_diff / 400.0))
        draw = clamp(0.265 - min(abs(elo_diff), 350.0) / 350.0 * 0.075, 0.17, 0.29)
        home = raw_home * (1.0 - draw)
        away = (1.0 - raw_home) * (1.0 - draw)
        total = home + away + draw
        home_prob = home / total
        away_prob = away / total
        strength = clamp(elo_diff / 420.0, -0.85, 0.85)
        expected_home = clamp(1.30 + 0.42 * strength, 0.45, 2.45)
        expected_away = clamp(1.08 - 0.34 * strength, 0.35, 2.30)
        confidence = clamp(55.0 + min(abs(elo_diff) / 38.0, 8.0), 53.0, 65.0)
        return MatchContext(
            source="clubelo",
            payload={"home_rating": home_row, "away_rating": away_row},
            expected_home=round(expected_home, 3),
            expected_away=round(expected_away, 3),
            home_win_probability=round(home_prob, 4),
            away_win_probability=round(away_prob, 4),
            confidence=float(round(confidence, 2)),
            details={
                "clubelo_home_elo": round(home_elo, 1),
                "clubelo_away_elo": round(away_elo, 1),
                "clubelo_elo_diff_home_adv": round(elo_diff, 1),
                "clubelo_draw_probability": round(draw, 4),
                "home_rating": round(home_elo, 1),
                "away_rating": round(away_elo, 1),
            },
        )

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}
