from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match
from app.utils import canonicalize_league_name, canonicalize_team_name, is_low_tier_league, is_simulated_or_esports_event


def _canonicalize_name(value: str) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    keep = []
    for ch in text:
        keep.append(ch if ch.isalnum() or ch.isspace() else " ")
    return " ".join("".join(keep).split())


def _league_key(value: str) -> str:
    return _canonicalize_name(value).replace(" ", "_")[:120] or "unknown"


class BookiesBootstrapProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (settings.bookies_api_base_url or "https://bookiesapi.com/api/get.php").rstrip("/")

    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.settings.bookies_api_enabled),
            "used_as_primary_source": False,
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "matches_built": 0,
            "low_tier_skipped": 0,
            "simulated_skipped": 0,
            "event_http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
        }
        preview: dict[str, Any] = {"sample_events": []}

        if not self.settings.bookies_api_enabled:
            return [], stats, preview

        token = self.settings.bookies_api_token or self.settings.bookies_api_key
        if not self.settings.bookies_api_login or not token:
            stats["response_errors"] += 1
            stats["last_body_preview"] = "Missing BOOKIES_API_LOGIN or BOOKIES_API_TOKEN/BOOKIES_API_KEY"
            return [], stats, preview

        days = max(1, min(int(self.settings.run_days_ahead or 1), 7))
        matches: list[Match] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=self.settings.bookies_api_timeout_seconds) as client:
            for offset in range(days):
                day_dt = datetime.now(UTC) + timedelta(days=offset)

                # BookiesAPI predatapage чаще ожидает дату формата DD.MM.YYYY
                day_candidates = [
                    day_dt.strftime("%d.%m.%Y"),
                    day_dt.strftime("%Y%m%d"),
                ]

                for page in range(1, max(1, self.settings.bookies_api_max_pages_per_day) + 1):
                    payload = None
                    last_items: list[dict[str, Any]] = []

                    for day in day_candidates:
                        params = {
                            "login": self.settings.bookies_api_login,
                            "token": token,
                            "task": "predatapage",
                            "sport": "soccer",
                            "day": day,
                            "p": page,
                        }
                        stats["requests"] += 1
                        try:
                            response = await client.get(self.base_url, params=params)
                        except Exception as exc:
                            stats["response_errors"] += 1
                            stats["last_body_preview"] = f"request failed: {exc}"
                            response = None

                        if response is None:
                            continue

                        stats["event_http_statuses"].append(response.status_code)
                        stats["last_body_preview"] = response.text[:1200]

                        if response.status_code != 200:
                            stats["response_errors"] += 1
                            continue

                        try:
                            payload = response.json()
                        except Exception:
                            stats["response_errors"] += 1
                            continue

                        shape = self._payload_shape(payload)
                        if shape not in stats["payload_shapes"]:
                            stats["payload_shapes"].append(shape)

                        items = self._get_event_list(payload)
                        if items:
                            last_items = items
                            break

                    items = last_items
                    stats["events_fetched"] += len(items)

                    if page == 1 and items:
                        preview["sample_events"] = items[:3]

                    if not items:
                        break

                    added_this_page = 0
                    for item in items:
                        try:
                            match = self._parse_match(item)
                        except Exception as exc:
                            stats["response_errors"] += 1
                            stats["last_body_preview"] = f"parse_match failed: {exc}; item={str(item)[:600]}"
                            continue

                        if match is None:
                            league_name = self._extract_league(item)
                            home_name, away_name = self._extract_teams(item)
                            if is_simulated_or_esports_event(home_name, away_name, league_name):
                                stats["simulated_skipped"] += 1
                            elif is_low_tier_league(league_name) and not self.settings.allow_low_tier:
                                stats["low_tier_skipped"] += 1
                            continue

                        key = match.match_key
                        if key in seen:
                            continue

                        seen.add(key)
                        matches.append(match)
                        added_this_page += 1

                    # Если записей мало — вероятно, страниц больше нет
                    if len(items) < max(1, self.settings.bookies_api_page_limit):
                        break

                    # Если со второй страницы и дальше ничего не добавили — выходим
                    if added_this_page == 0 and page >= 2:
                        break

        stats["matches_built"] = len(matches)
        return matches, stats, preview

    @staticmethod
    def _payload_shape(payload: Any) -> str:
        if isinstance(payload, dict):
            return ",".join(sorted(map(str, payload.keys()))[:12])
        return type(payload).__name__

    def _get_event_list(self, payload: Any) -> list[dict[str, Any]]:
        if not payload:
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            # ВАЖНО: у predatapage BookiesAPI матчи приходят именно в games_pre
            for key in (
                "games_pre",
                "data",
                "results",
                "response",
                "games",
                "matches",
                "events",
                "fixtures",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

        return []

    def _parse_match(self, item: dict[str, Any]) -> Match | None:
        game_id = (
            item.get("game_id")
            or item.get("gameId")
            or item.get("id")
            or item.get("match_id")
            or item.get("fixture_id")
            or item.get("event_id")
        )

        home, away = self._extract_teams(item)
        if not game_id or not home or not away:
            return None

        commence_time = self._parse_datetime(item)
        if commence_time is None:
            return None

        league = self._extract_league(item)
        if is_simulated_or_esports_event(home, away, league):
            return None
        low_tier = is_low_tier_league(league)
        if low_tier and not self.settings.allow_low_tier:
            return None

        metadata = {
            "bootstrap": True,
            "raw_game_id": str(game_id),
        }
        if item.get("bet365_id"):
            metadata["bet365_id"] = str(item.get("bet365_id"))

        return Match(
            source="bookies_api",
            source_event_id=str(game_id),
            sport_key="soccer",
            league_name=league or "Unknown",
            home_team=home,
            away_team=away,
            commence_time=commence_time,
            home_team_norm=canonicalize_team_name(home),
            away_team_norm=canonicalize_team_name(away),
            league_key=canonicalize_league_name(league),
            tier="low" if low_tier else "mid",
            metadata=metadata,
        )

    def _extract_teams(self, item: dict[str, Any]) -> tuple[str, str]:
        def team_name(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, (str, int, float)):
                return str(value)
            if isinstance(value, dict):
                for key in (
                    "name",
                    "team_name",
                    "teamName",
                    "title",
                    "short_name",
                    "shortName",
                    "common_name",
                    "commonName",
                    "en_name",
                    "enName",
                    "slug",
                    "label",
                    "full_name",
                    "fullName",
                    "abbr",
                    "code",
                ):
                    if value.get(key):
                        return str(value[key])
            return ""

        home_candidates = [
            item.get("home_team"),
            item.get("homeTeam"),
            item.get("home"),
            item.get("team_home"),
            item.get("team1"),
            item.get("team1_name"),
            item.get("home_name"),
            item.get("opponent1"),
            item.get("opponent1_name"),
            item.get("opp_1"),
            item.get("localteam"),
            item.get("localteam_name"),
            item.get("local"),
            item.get("local_name"),
        ]
        away_candidates = [
            item.get("away_team"),
            item.get("awayTeam"),
            item.get("away"),
            item.get("team_away"),
            item.get("team2"),
            item.get("team2_name"),
            item.get("away_name"),
            item.get("opponent2"),
            item.get("opponent2_name"),
            item.get("opp_2"),
            item.get("visitorteam"),
            item.get("visitorteam_name"),
            item.get("visitor"),
            item.get("visitor_name"),
        ]

        teams = item.get("teams")
        if isinstance(teams, dict):
            home_candidates.extend([teams.get("home"), teams.get("local"), teams.get("team1")])
            away_candidates.extend([teams.get("away"), teams.get("visitor"), teams.get("team2")])
        elif isinstance(teams, list) and len(teams) >= 2:
            home_candidates.append(teams[0])
            away_candidates.append(teams[1])

        participants = item.get("participants")
        if isinstance(participants, list) and len(participants) >= 2:
            home_candidates.append(participants[0])
            away_candidates.append(participants[1])

        competitors = item.get("competitors")
        if isinstance(competitors, list) and len(competitors) >= 2:
            home_candidates.append(competitors[0])
            away_candidates.append(competitors[1])

        home = next((team_name(v) for v in home_candidates if team_name(v)), "")
        away = next((team_name(v) for v in away_candidates if team_name(v)), "")
        return home.strip(), away.strip()

    def _extract_league(self, item: dict[str, Any]) -> str:
        league = item.get("league")
        if isinstance(league, dict):
            for key in ("name", "title", "league_name"):
                if league.get(key):
                    return str(league[key])

        competition = item.get("competition")
        if isinstance(competition, dict):
            for key in ("name", "title"):
                if competition.get(key):
                    return str(competition[key])

        tournament = item.get("tournament")
        if isinstance(tournament, dict):
            for key in ("name", "title"):
                if tournament.get(key):
                    return str(tournament[key])

        for key in ("league_name", "competition_name", "tournament_name", "championship"):
            if item.get(key):
                return str(item[key])

        if isinstance(item.get("league"), str):
            return str(item["league"])
        if isinstance(item.get("competition"), str):
            return str(item["competition"])

        return ""

    def _parse_datetime(self, item: dict[str, Any]) -> datetime | None:
        raw = (
            item.get("event_date")
            or item.get("start_time")
            or item.get("commence_time")
            or item.get("kickoff")
            or item.get("date")
            or item.get("match_time")
            or item.get("time")
            or item.get("datetime")
            or item.get("start")
            or item.get("starts_at")
            or item.get("startAt")
            or item.get("match_date")
            or item.get("event_time")
            or item.get("ts")
            or item.get("timestamp")
        )

        if not raw and item.get("date_start") and item.get("time_start"):
            raw = f"{item['date_start']} {item['time_start']}"
        if not raw and item.get("date") and item.get("time"):
            raw = f"{item['date']} {item['time']}"

        if raw is None or raw == "":
            return None

        if isinstance(raw, (int, float)):
            number = int(raw)
            if number > 1_000_000_000:
                if number < 1_000_000_000_000:
                    return datetime.fromtimestamp(number, tz=UTC)
                return datetime.fromtimestamp(number / 1000, tz=UTC)
            raw = str(raw)

        text = str(raw).strip()

        # Unix timestamp строкой
        if text.isdigit() and len(text) in (10, 13):
            number = int(text)
            if len(text) == 10:
                return datetime.fromtimestamp(number, tz=UTC)
            return datetime.fromtimestamp(number / 1000, tz=UTC)

        # DD.MM.YYYY HH:MM[:SS]
        for fmt in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.replace(tzinfo=UTC)
            except Exception:
                pass

        text = text.replace("/", "-")
        if "T" not in text and " " in text:
            text = text.replace(" ", "T", 1)

        for candidate in (text, text + "Z"):
            try:
                dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except Exception:
                continue

        return None
