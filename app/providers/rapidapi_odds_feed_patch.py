from __future__ import annotations

"""Runtime hardening for RapidAPI OddsFeed bridge.

Smoke on 2026-05-04 showed:
- odds-feed.p.rapidapi.com /api/v1/sports and /api/v1/events respond with 200;
- sportapi7 is monthly rate-limited;
- sportsbook_api is an arbitrage/control API, not a normal football fixtures feed;
- free-api-live-football-data is useful for lookup/context but not odds.

This patch therefore enables only OddsFeed inside RapidApiOddsBridgeProvider,
adds football-specific event paths, and teaches the generic parser the OddsFeed
schema: team_home/team_away/start_at/main_outcome_0/1/2.
"""

import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

PATCH_MARKER = "_harizon_rapidapi_odds_feed_patch_v1"
UTC = timezone.utc


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        num = float(str(value).replace(",", "."))
        return num if num > 1.0 else None
    except Exception:
        return None


def _dict_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "slug", "short_name", "shortName"):
            raw = value.get(key)
            if raw not in (None, ""):
                return str(raw).strip()
    if value not in (None, ""):
        return str(value).strip()
    return ""


def _football_row(row: dict[str, Any]) -> bool:
    sport = row.get("sport")
    text = _dict_name(sport).lower()
    if not text:
        return True
    return "football" in text or "soccer" in text


def install() -> bool:
    try:
        from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider
        from app.schemas import Offer
    except Exception:
        return False
    if getattr(RapidApiOddsBridgeProvider, PATCH_MARKER, False):
        return False

    original_build_providers = RapidApiOddsBridgeProvider._build_providers
    original_pick_text = RapidApiOddsBridgeProvider._pick_text
    original_event_start = RapidApiOddsBridgeProvider._event_start
    original_match_event = RapidApiOddsBridgeProvider._match_event
    original_parse = RapidApiOddsBridgeProvider._parse_offers_from_payload

    def build_providers_patched(self: Any) -> list[dict[str, Any]]:
        providers = list(original_build_providers(self) or [])
        allowed_raw = os.getenv("RAPIDAPI_ODDS_BRIDGE_ALLOWED_PROVIDERS") or "odds_feed"
        allowed = {item.strip().lower() for item in allowed_raw.split(",") if item.strip()}
        out: list[dict[str, Any]] = []
        for provider in providers:
            key = str(provider.get("key") or "").strip().lower()
            if key not in allowed:
                continue
            if key == "odds_feed":
                provider = dict(provider)
                provider["max_requests"] = int(float(os.getenv("ODDS_FEED_RAPIDAPI_PER_RUN_MAX") or "3"))
                provider["event_paths"] = [
                    "/api/v1/events?sport=football&per_page=100",
                    "/api/v1/events?sport_id=1&per_page=100",
                    "/api/v1/events?sport_slug=football&per_page=100",
                    "/api/v1/events?slug=football&per_page=100",
                    "/api/v1/events?sport=soccer&per_page=100",
                    "/api/v1/events?per_page=100",
                ]
                provider["market_paths"] = [
                    "/api/v1/events/{event_key}/markets",
                    "/api/v1/event/{event_key}/markets",
                    "/api/v1/event-markets?event_id={event_key}",
                    "/api/event-markets?event_id={event_key}",
                ]
            out.append(provider)
        return out

    @staticmethod
    def pick_text_patched(row: dict[str, Any], keys: list[str]) -> str:
        expanded: list[str] = []
        for key in keys:
            expanded.append(key)
            if key == "home_team":
                expanded.extend(["team_home", "homeTeam", "home"])
            if key == "away_team":
                expanded.extend(["team_away", "awayTeam", "away"])
            if key == "league":
                expanded.extend(["tournament", "category", "competition", "season"])
        seen: set[str] = set()
        for key in expanded:
            if key in seen:
                continue
            seen.add(key)
            value = row.get(key)
            text = _dict_name(value)
            if text:
                return text
        return original_pick_text(row, keys)

    @staticmethod
    def event_start_patched(row: dict[str, Any]):  # type: ignore[no-untyped-def]
        patched_row = dict(row)
        if "start_at" in patched_row and "startAt" not in patched_row:
            patched_row["startAt"] = patched_row.get("start_at")
        if "start_at_timestamp" in patched_row and "startTimestamp" not in patched_row:
            patched_row["startTimestamp"] = patched_row.get("start_at_timestamp")
        return original_event_start(patched_row)

    def match_event_patched(self: Any, row: dict[str, Any], matches: list[Any]):  # type: ignore[no-untyped-def]
        if isinstance(row, dict) and not _football_row(row):
            return None
        return original_match_event(self, row, matches)

    def oddsfeed_inline_offers(self: Any, payload: Any, match: Any, source: str, event_key: str | None) -> list[Any]:
        if source != "odds_feed" or not isinstance(payload, dict):
            return []
        offers: list[Any] = []
        market_name = "Match Winner"
        mapping = [
            ("main_outcome_0", getattr(match, "home_team", "Home"), "home"),
            ("main_outcome_1", "Draw", None),
            ("main_outcome_2", getattr(match, "away_team", "Away"), "away"),
        ]
        for key, selection, team_side in mapping:
            price = _float(payload.get(key))
            if price is None:
                continue
            offers.append(Offer(
                source="odds_feed",
                bookmaker="OddsFeed",
                family="h2h",
                selection=str(selection),
                price=float(price),
                point=None,
                team_side=team_side,
                market_name=market_name,
                market_key=f"odds_feed:{key}",
                source_event_id=str(event_key or payload.get("id") or ""),
                metadata={
                    "rapidapi_source": "odds_feed",
                    "oddsfeed_event_id": payload.get("id"),
                    "oddsfeed_sport": _dict_name(payload.get("sport")),
                    "oddsfeed_tournament": _dict_name(payload.get("tournament")),
                    "oddsfeed_inline_main_outcome": key,
                },
            ))
        return offers

    def parse_offers_patched(self: Any, payload: Any, match: Any, source: str, event_key: str | None) -> list[Any]:
        offers = list(original_parse(self, payload, match, source, event_key) or [])
        extra = oddsfeed_inline_offers(self, payload, match, source, event_key)
        if extra:
            seen = {(o.bookmaker, o.family, o.selection, o.point, o.price) for o in offers}
            for item in extra:
                key = (item.bookmaker, item.family, item.selection, item.point, item.price)
                if key not in seen:
                    offers.append(item)
                    seen.add(key)
        return offers

    RapidApiOddsBridgeProvider._build_providers = build_providers_patched
    RapidApiOddsBridgeProvider._pick_text = pick_text_patched
    RapidApiOddsBridgeProvider._event_start = event_start_patched
    RapidApiOddsBridgeProvider._match_event = match_event_patched
    RapidApiOddsBridgeProvider._parse_offers_from_payload = parse_offers_patched
    setattr(RapidApiOddsBridgeProvider, PATCH_MARKER, True)
    return True
