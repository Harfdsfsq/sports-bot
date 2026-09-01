from __future__ import annotations

"""Windowed core coverage runtime layer for HARIZON.

This patch makes the run behave like a 00:00 inventory + 2-hour cron system:
- split the daily inventory into 4-hour kickoff windows;
- prioritize the current window, then the next windows;
- force odds_api_io, SStats and Bzzoiro to be the main coverage providers;
- enrich near-window matches even when a match has only partial lines yet;
- require 2+ odds sources and 2+ context sources before publication;
- require line-movement history unless the match starts before the next cron pass.

It is intentionally additive: it does not relax EV, confidence, price-integrity or
market-family guards.
"""

import asyncio
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.schemas import Match, MatchContext, Offer
from app.utils import clamp, parse_datetime, score_event_match_variants

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
LATEST_REPORT = EXPORT_DIR / "latest-windowed-core-coverage.json"
SNAPSHOT_PATH = ROOT / ".data" / "odds_movement_snapshots.jsonl"
_INSTALLED = False

CORE_PROVIDERS = {"odds_api_io", "sstats", "bzzoiro"}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).strip().replace(",", "."))
        if math.isfinite(number):
            return number
    except Exception:
        return None
    return None


def _norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_provider(value: Any) -> str:
    raw = _norm_text(value)
    if not raw:
        return ""
    if raw.startswith("odds_api_io") or raw in {"oddsapiio", "odds_api"}:
        return "odds_api_io"
    if "bzzoiro" in raw or raw.startswith("bsd"):
        return "bzzoiro"
    if "sstats" in raw or raw.startswith("sstats_form"):
        return "sstats"
    if "football_data" in raw:
        return "football_data"
    if "thesportsdb" in raw or "sportsdb" in raw:
        return "thesportsdb"
    if "sportlogic" in raw:
        return "sportlogic"
    if "rapidapi" in raw:
        return "rapidapi_odds_bridge"
    if "weather" in raw or "open_meteo" in raw:
        return "weather"
    if "news" in raw or "gnews" in raw or "currents" in raw:
        return "news"
    if raw == "ensemble":
        return "ensemble"
    return raw


def _flatten(value: Any, depth: int = 0) -> list[Any]:
    if depth > 5:
        return []
    if isinstance(value, dict):
        rows: list[Any] = []
        for key, item in value.items():
            rows.append(key)
            rows.extend(_flatten(item, depth + 1))
        return rows
    if isinstance(value, (list, tuple, set)):
        rows = []
        for item in list(value)[:80]:
            rows.extend(_flatten(item, depth + 1))
        return rows
    return [value]


def _provider_tokens_from_payload(value: Any) -> set[str]:
    text = " ".join(str(item) for item in _flatten(value) if item not in (None, ""))[:25000].lower()
    found: set[str] = set()
    for provider in ("odds_api_io", "sstats", "bzzoiro", "football_data", "thesportsdb", "sportlogic", "weather", "news"):
        if provider in text or provider.replace("_", "") in text.replace("_", ""):
            found.add(_norm_provider(provider))
    if "open-meteo" in text or "open_meteo" in text:
        found.add("weather")
    return {item for item in found if item}


def _settings_tz(settings: Any | None = None) -> timezone | ZoneInfo:
    if settings is not None:
        tzinfo = getattr(settings, "tzinfo", None)
        if tzinfo is not None:
            return tzinfo
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(str(name))
    except Exception:
        return UTC


def _window_hours() -> int:
    return max(1, _to_int(os.getenv("CORE_COVERAGE_WINDOW_HOURS") or 4, 4))


def _cron_interval_hours() -> int:
    return max(1, _to_int(os.getenv("CORE_COVERAGE_CRON_INTERVAL_HOURS") or 2, 2))


def _current_window_start(now_utc: datetime, settings: Any | None = None) -> datetime:
    tz = _settings_tz(settings)
    local = now_utc.astimezone(tz)
    wh = _window_hours()
    start_hour = (local.hour // wh) * wh
    return local.replace(hour=start_hour, minute=0, second=0, microsecond=0)


def _window_index(match: Match | Any, now_utc: datetime, settings: Any | None = None) -> int:
    try:
        kickoff = getattr(match, "commence_time").astimezone(_settings_tz(settings))
    except Exception:
        return 999
    start = _current_window_start(now_utc, settings)
    delta_hours = (kickoff - start).total_seconds() / 3600.0
    if delta_hours < -0.25:
        return 999
    return max(0, int(delta_hours // _window_hours()))


def _next_cron_time(now_utc: datetime, settings: Any | None = None) -> datetime:
    tz = _settings_tz(settings)
    local = now_utc.astimezone(tz)
    interval = _cron_interval_hours()
    base = local.replace(minute=0, second=0, microsecond=0)
    next_hour = ((local.hour // interval) + 1) * interval
    if next_hour >= 24:
        next_local = (base + timedelta(days=1)).replace(hour=0)
    else:
        next_local = base.replace(hour=next_hour)
    return next_local.astimezone(UTC)


def _match_priority(match: Match | Any, now_utc: datetime, settings: Any | None = None) -> tuple[Any, ...]:
    try:
        kickoff = getattr(match, "commence_time").astimezone(UTC)
        minutes = (kickoff - now_utc).total_seconds() / 60.0
    except Exception:
        minutes = 999999.0
    tier = str(getattr(match, "tier", "mid") or "mid").lower()
    tier_rank = 0 if tier == "top" else 1 if tier == "mid" else 2
    urgent = 0 if -10 <= minutes <= (_window_hours() * 60) else 1
    return (
        _window_index(match, now_utc, settings),
        urgent,
        max(0.0, minutes),
        tier_rank,
        str(getattr(match, "league_name", "")).lower(),
        str(getattr(match, "home_team", "")).lower(),
    )


def _prioritize_matches(matches: list[Match], now_utc: datetime | None = None, settings: Any | None = None, limit: int | None = None) -> list[Match]:
    now = now_utc or datetime.now(UTC)
    rows = sorted([m for m in matches or [] if getattr(m, "sport_key", "") == "soccer"], key=lambda m: _match_priority(m, now, settings))
    if limit is not None and limit > 0:
        return rows[:limit]
    return rows


def _unique_matches(*groups: list[Match]) -> list[Match]:
    out: list[Match] = []
    seen: set[str] = set()
    for group in groups:
        for match in group or []:
            key = str(getattr(match, "match_key", "") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(match)
    return out


def _context_sources(value: Any) -> set[str]:
    sources: set[str] = set()
    if isinstance(value, MatchContext):
        src = _norm_provider(value.source)
        if src and src != "ensemble":
            sources.add(src)
        sources |= _provider_tokens_from_payload(value.details)
        sources |= _provider_tokens_from_payload(value.payload)
        return {item for item in sources if item}
    if isinstance(value, dict):
        for item in value.values():
            sources |= _context_sources(item)
        sources |= _provider_tokens_from_payload(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            sources |= _context_sources(item)
    else:
        src = _norm_provider(value)
        if src and src != "ensemble":
            sources.add(src)
    return {item for item in sources if item}


def _offer_sources_from_candidate(candidate: Any) -> set[str]:
    summary = dict(getattr(candidate, "source_summary", {}) or {})
    sources: set[str] = set()
    for item in summary.get("sources") or []:
        src = _norm_provider(item)
        if src:
            sources.add(src)
    for key in ("selected_source", "source", "provider"):
        src = _norm_provider(summary.get(key))
        if src:
            sources.add(src)
    for row in getattr(candidate, "raw_bucket_offers", []) or []:
        if isinstance(row, dict):
            src = _norm_provider(row.get("source"))
            if src:
                sources.add(src)
    return {item for item in sources if item and item != "ensemble"}


def _offer_sources_from_map(offers: Any) -> set[str]:
    sources: set[str] = set()
    for offer in offers or []:
        if isinstance(offer, Offer):
            src = _norm_provider(offer.source)
        elif isinstance(offer, dict):
            src = _norm_provider(offer.get("source"))
        else:
            src = ""
        if src:
            sources.add(src)
    return {item for item in sources if item and item != "ensemble"}


def _selection_key(candidate: Any) -> str:
    family = _norm_text(getattr(candidate, "family", ""))
    selection_key = _norm_text(getattr(candidate, "selection_key", "") or getattr(candidate, "selection", ""))
    point = getattr(candidate, "point", None)
    try:
        point_text = "" if point is None else f"{float(point):.2f}"
    except Exception:
        point_text = str(point or "")
    team_side = _norm_text(getattr(candidate, "team_side", ""))
    return f"{family}|{selection_key}|{point_text}|{team_side}"


def _snapshot_selection_key(row: dict[str, Any]) -> str:
    family = _norm_text(row.get("family"))
    selection = _norm_text(row.get("selection"))
    if selection in {"yes", "no", "over", "under", "home", "away", "draw"}:
        selection_key = selection
    elif "over" in selection:
        selection_key = "over"
    elif "under" in selection:
        selection_key = "under"
    else:
        selection_key = selection
    point = row.get("point")
    try:
        point_text = "" if point in (None, "") else f"{float(point):.2f}"
    except Exception:
        point_text = str(point or "")
    team_side = _norm_text(row.get("team_side"))
    return f"{family}|{selection_key}|{point_text}|{team_side}"


def _load_snapshot_history(match_key: str, candidate: Any) -> list[dict[str, Any]]:
    if not SNAPSHOT_PATH.exists():
        return []
    target_key = _selection_key(candidate)
    rows: list[dict[str, Any]] = []
    try:
        for line in SNAPSHOT_PATH.read_text(encoding="utf-8").splitlines()[-20000:]:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("match_key") or "") != str(match_key):
                continue
            if _snapshot_selection_key(row) != target_key:
                continue
            price = _to_float(row.get("price"))
            captured = row.get("captured_at_utc")
            if price is None or not captured:
                continue
            rows.append(row)
    except Exception:
        return []
    rows.sort(key=lambda item: str(item.get("captured_at_utc") or ""))
    return rows


def _movement_status(candidate: Any, now_utc: datetime, settings: Any | None = None) -> dict[str, Any]:
    kickoff = getattr(candidate, "commence_time", None)
    if not isinstance(kickoff, datetime):
        return {"ok": False, "reason": "missing_kickoff"}
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)
    kickoff = kickoff.astimezone(UTC)
    next_cron = _next_cron_time(now_utc, settings)
    recheck_lead = max(0, _to_int(os.getenv("CORE_COVERAGE_RECHECK_LEAD_MINUTES") or 20, 20))
    if kickoff <= next_cron + timedelta(minutes=recheck_lead):
        return {
            "ok": True,
            "reason": "urgent_before_next_cron_or_too_little_recheck_lead",
            "next_cron_utc": next_cron.isoformat(),
            "history_required": False,
        }
    history = _load_snapshot_history(str(getattr(candidate, "match_key", "")), candidate)
    distinct_times = sorted({str(row.get("captured_at_utc") or "") for row in history})
    if len(distinct_times) < max(2, _to_int(os.getenv("CORE_COVERAGE_MIN_MOVEMENT_SNAPSHOTS") or 2, 2)):
        return {
            "ok": False,
            "reason": "needs_next_cron_line_movement_recheck",
            "next_cron_utc": next_cron.isoformat(),
            "history_required": True,
            "snapshot_count": len(distinct_times),
        }
    current_price = _to_float(getattr(candidate, "odds", None))
    previous_prices = [_to_float(row.get("price")) for row in history[:-1]]
    previous_prices = [p for p in previous_prices if p is not None]
    if current_price is not None and previous_prices:
        previous = previous_prices[-1]
        max_drop_pct = max(0.0, float(os.getenv("CORE_COVERAGE_MAX_ALLOWED_PRICE_DROP_PCT") or 8.0))
        if current_price < previous * (1.0 - max_drop_pct / 100.0):
            return {
                "ok": False,
                "reason": "price_dropped_too_much_since_previous_window",
                "snapshot_count": len(distinct_times),
                "previous_price": round(previous, 4),
                "current_price": round(current_price, 4),
                "max_drop_pct": max_drop_pct,
            }
    return {
        "ok": True,
        "reason": "movement_rechecked_across_cron_windows",
        "history_required": True,
        "snapshot_count": len(distinct_times),
        "next_cron_utc": next_cron.isoformat(),
    }


def _write_report(payload: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        LATEST_REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _response_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except Exception:
        return None


def _results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("results", "events", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _event_home_away(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    home = [
        _field(row, "home_team", "home", "home_name", "homeTeam", "home_team_name"),
        _field(row, "home_team_name_ru", "home_short_name"),
    ]
    away = [
        _field(row, "away_team", "away", "away_name", "awayTeam", "away_team_name"),
        _field(row, "away_team_name_ru", "away_short_name"),
    ]
    for obj_key, target in (("home_team_obj", home), ("away_team_obj", away), ("homeTeam", home), ("awayTeam", away)):
        obj = row.get(obj_key)
        if isinstance(obj, dict):
            target.extend([obj.get("name"), obj.get("short_name"), obj.get("display_name")])
    return ([str(x).strip() for x in home if str(x or "").strip()], [str(x).strip() for x in away if str(x or "").strip()])


def _event_league(row: dict[str, Any]) -> str:
    league = row.get("league")
    if isinstance(league, dict):
        return str(league.get("name") or league.get("display_name") or "").strip()
    return str(row.get("league_name") or row.get("competition") or league or "").strip()


def _event_start(row: dict[str, Any]) -> datetime | None:
    raw = _field(row, "event_date", "start_time", "date", "commence_time", "kickoff")
    if raw in (None, ""):
        return None
    try:
        dt = parse_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _match_bzzoiro_event(match: Match, events: list[dict[str, Any]], settings: Any | None = None) -> tuple[dict[str, Any] | None, float, str | None]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_quality: str | None = None
    exact_tol = float(getattr(settings, "match_start_tolerance_hours", 12.0) or 12.0)
    fuzzy_tol = float(getattr(settings, "fallback_match_start_tolerance_hours", 8.0) or 8.0)
    for event in events:
        home_candidates, away_candidates = _event_home_away(event)
        start = _event_start(event)
        if not home_candidates or not away_candidates or start is None:
            continue
        score, quality, _, _ = score_event_match_variants(
            sport="soccer",
            match_home=match.home_team,
            match_away=match.away_team,
            match_start=match.commence_time,
            match_league=match.league_name,
            event_home_candidates=home_candidates,
            event_away_candidates=away_candidates,
            event_start=start,
            event_league=_event_league(event),
            exact_tolerance_hours=exact_tol,
            fuzzy_tolerance_hours=max(fuzzy_tol, 18.0),
        )
        if score > best_score:
            best = event
            best_score = score
            best_quality = quality
    if best is None or best_score < (68.0 if best_quality == "fuzzy" else 62.0):
        return None, best_score, best_quality
    return best, best_score, best_quality


def _extract_bzzoiro_xg(resources: dict[str, Any]) -> tuple[float | None, float | None]:
    stats = resources.get("stats") if isinstance(resources, dict) else None
    if not isinstance(stats, dict):
        return None, None
    candidates = [stats]
    if isinstance(stats.get("stats"), dict):
        candidates.append(stats["stats"])
    for item in candidates:
        home = item.get("home") if isinstance(item.get("home"), dict) else {}
        away = item.get("away") if isinstance(item.get("away"), dict) else {}
        home_xg = _to_float((home.get("xg") or {}).get("actual") if isinstance(home.get("xg"), dict) else home.get("xg"))
        away_xg = _to_float((away.get("xg") or {}).get("actual") if isinstance(away.get("xg"), dict) else away.get("xg"))
        if home_xg is not None or away_xg is not None:
            return home_xg, away_xg
    return None, None


def _bzzoiro_odds_hints(resources: dict[str, Any]) -> list[dict[str, Any]]:
    odds_payload = resources.get("odds") if isinstance(resources, dict) else None
    if not isinstance(odds_payload, dict):
        return []
    odds = odds_payload.get("odds") if isinstance(odds_payload.get("odds"), dict) else odds_payload
    mapping = {
        "home_win": ("h2h", "home", None),
        "draw": ("h2h", "draw", None),
        "away_win": ("h2h", "away", None),
        "over_15_goals": ("totals", "Over", 1.5),
        "under_15_goals": ("totals", "Under", 1.5),
        "over_25_goals": ("totals", "Over", 2.5),
        "under_25_goals": ("totals", "Under", 2.5),
        "over_35_goals": ("totals", "Over", 3.5),
        "under_35_goals": ("totals", "Under", 3.5),
        "btts_yes": ("btts", "Yes", None),
        "btts_no": ("btts", "No", None),
    }
    hints: list[dict[str, Any]] = []
    for key, (family, selection, point) in mapping.items():
        price = _to_float(odds.get(key)) if isinstance(odds, dict) else None
        if price is None or price <= 1.0:
            continue
        hints.append({
            "source": "bzzoiro",
            "bookmaker": "BzzoiroConsensus",
            "family": family,
            "selection": selection,
            "price": round(price, 4),
            "point": point,
            "market_name": key,
            "market_key": family,
        })
    return hints


def _find_numeric_id(value: Any, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key in ("id", "Id", "gameId", "GameId", "game_id", "GameID"):
            raw = value.get(key)
            if raw not in (None, "") and str(raw).strip().isdigit():
                return str(raw).strip()
        for item in value.values():
            found = _find_numeric_id(item, depth + 1)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value[:20]:
            found = _find_numeric_id(item, depth + 1)
            if found:
                return found
    return None


def _apply_env_defaults() -> None:
    defaults = {
        "WINDOWED_CORE_COVERAGE_ENABLED": "true",
        "CORE_COVERAGE_WINDOW_HOURS": "4",
        "CORE_COVERAGE_CRON_INTERVAL_HOURS": "2",
        "CORE_COVERAGE_RECHECK_LEAD_MINUTES": "20",
        "CORE_COVERAGE_MIN_ODDS_SOURCES": "2",
        "CORE_COVERAGE_MIN_CONTEXT_SOURCES": "2",
        "CORE_COVERAGE_MIN_CORE_PROVIDERS": "2",
        "CORE_COVERAGE_MAIN_PROVIDERS": "odds_api_io,sstats,bzzoiro",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "ENABLE_ODDS_API_IO": "true",
        "SSTATS_ENABLED": "true",
        "ENABLE_SSTATS_CONTEXT": "true",
        "ENABLE_BZZOIRO_CONTEXT": "true",
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "160",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "80",
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "80",
        "MAX_MATCHES_FOR_ODDS_FETCH": "900",
        "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "90",
        "SSTATS_DEEP_ENDPOINTS_ENABLED": "true",
        "SSTATS_DEEP_REQUESTS_MAX_PER_RUN": "45",
        "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "120",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "240",
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true",
        "BZZOIRO_V2_MATCH_LIMIT": "64",
        "BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN": "120",
        "BZZOIRO_V2_EVENTS_ENABLED": "true",
        "BZZOIRO_V2_STATS_ENABLED": "true",
        "BZZOIRO_V2_METADATA_ENABLED": "true",
        "BZZOIRO_V2_LINEUPS_ENABLED": "true",
        "BZZOIRO_V2_ODDS_ENABLED": "true",
        "ODDS_MOVEMENT_SNAPSHOTS_ENABLED": "true",
        "PUBLISH_ALLOW_B_TIER": "true",
        "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
        "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MIN_SOURCES_PUBLISH": "1",
        "MIN_BOOKS_PUBLISH": "2",
    }
    for key, value in defaults.items():
        os.environ[key] = str(value)


def _patch_odds_api_io_priority() -> None:
    try:
        from app.providers.odds_api_io import OddsApiIoProvider
    except Exception:
        return
    if getattr(OddsApiIoProvider, "_harizon_windowed_priority_patch", False):
        return

    def patched_priority(self, match: Match, now: datetime) -> tuple[Any, ...]:  # type: ignore[no-untyped-def]
        return _match_priority(match, now, getattr(self, "settings", None))

    OddsApiIoProvider._match_priority = patched_priority  # type: ignore[assignment]
    OddsApiIoProvider._harizon_windowed_priority_patch = True


def _patch_bzzoiro_v2() -> None:
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception:
        return
    if getattr(BzzoiroContextProvider, "_harizon_windowed_v2_patch", False):
        return
    original_fetch_context = BzzoiroContextProvider.fetch_context

    def priority(self, matches: list[Match]) -> list[Match]:  # type: ignore[no-untyped-def]
        return _prioritize_matches(matches, datetime.now(UTC), getattr(self, "settings", None))

    async def patched_fetch_context(self, matches: list[Match]):  # type: ignore[no-untyped-def]
        contexts, stats, preview = await original_fetch_context(self, matches)
        stats = dict(stats or {})
        preview = dict(preview or {})
        if not _truthy(os.getenv("BZZOIRO_V2_ENRICHMENT_ENABLED"), True):
            return contexts, stats, preview
        token = str(os.getenv("BZZOIRO_API_KEY") or getattr(getattr(self, "settings", None), "bzzoiro_api_key", "") or "").strip()
        if not token or not matches:
            return contexts, stats, preview
        now = datetime.now(UTC)
        target_limit = max(1, _to_int(os.getenv("BZZOIRO_V2_MATCH_LIMIT") or 64, 64))
        target_matches = _prioritize_matches([m for m in matches if getattr(m, "sport_key", "") == "soccer"], now, getattr(self, "settings", None), target_limit)
        if not target_matches:
            return contexts, stats, preview
        max_requests = max(0, _to_int(os.getenv("BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN") or 120, 120))
        used = 0
        v2_stats = {"enabled": True, "target_matches": len(target_matches), "requests": 0, "events_fetched": 0, "contexts_built": 0, "odds_resources": 0, "stats_resources": 0, "metadata_resources": 0, "lineups_resources": 0, "errors": 0}
        headers = {"Authorization": f"Token {token}"}
        min_dt = min(m.commence_time for m in target_matches).astimezone(UTC)
        max_dt = max(m.commence_time for m in target_matches).astimezone(UTC)
        events: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=float(getattr(getattr(self, "settings", None), "bzzoiro_timeout_seconds", 20.0) or 20.0)) as client:
                offset = 0
                while used < max_requests and offset <= 600:
                    params = {"date_from": min_dt.date().isoformat(), "date_to": max_dt.date().isoformat(), "limit": 200, "offset": offset}
                    used += 1; v2_stats["requests"] += 1
                    response = await client.get("https://sports.bzzoiro.com/api/v2/events/", headers=headers, params=params)
                    payload = _response_json(response)
                    batch = _results(payload)
                    if response.status_code != 200 or not batch:
                        if response.status_code != 200:
                            v2_stats["errors"] += 1
                        break
                    events.extend(batch)
                    if not isinstance(payload, dict) or not payload.get("next"):
                        break
                    offset += 200
                v2_stats["events_fetched"] = len(events)
                for match in target_matches:
                    if used >= max_requests:
                        break
                    event, score, quality = _match_bzzoiro_event(match, events, getattr(self, "settings", None))
                    if not event:
                        continue
                    event_id = event.get("id") or event.get("event_id")
                    if event_id in (None, ""):
                        continue
                    resources: dict[str, Any] = {"event": event, "match_score": round(score, 3), "match_quality": quality}
                    endpoints = []
                    if _truthy(os.getenv("BZZOIRO_V2_STATS_ENABLED"), True):
                        endpoints.append(("stats", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/stats/"))
                    if _truthy(os.getenv("BZZOIRO_V2_METADATA_ENABLED"), True):
                        endpoints.append(("metadata", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/metadata/"))
                    if _truthy(os.getenv("BZZOIRO_V2_LINEUPS_ENABLED"), True):
                        endpoints.append(("lineups", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/lineups/"))
                    if _truthy(os.getenv("BZZOIRO_V2_ODDS_ENABLED"), True):
                        endpoints.append(("odds", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/odds/"))
                    for name, url in endpoints:
                        if used >= max_requests:
                            break
                        used += 1; v2_stats["requests"] += 1
                        try:
                            response = await client.get(url, headers=headers)
                            if response.status_code == 200:
                                resources[name] = _response_json(response)
                                v2_stats[f"{name}_resources"] = int(v2_stats.get(f"{name}_resources", 0) or 0) + 1
                            else:
                                v2_stats["errors"] += 1
                        except Exception:
                            v2_stats["errors"] += 1
                    home_xg, away_xg = _extract_bzzoiro_xg(resources)
                    hints = _bzzoiro_odds_hints(resources)
                    context = MatchContext(
                        source="bzzoiro_v2",
                        payload=resources,
                        expected_home=home_xg,
                        expected_away=away_xg,
                        confidence=66.0 if home_xg is not None or away_xg is not None else 60.0,
                        details={
                            "bzzoiro_v2": True,
                            "bzzoiro_event_id": event_id,
                            "bzzoiro_match_score": round(score, 3),
                            "bzzoiro_match_quality": quality,
                            "provider_odds_hints": hints,
                            "provider_odds_hints_count": len(hints),
                            "resources_loaded": sorted(k for k in resources.keys() if k not in {"event", "match_score", "match_quality"}),
                        },
                    )
                    existing = contexts.get(match.match_key) if isinstance(contexts, dict) else None
                    if existing is None or _norm_provider(getattr(existing, "source", "")) != "bzzoiro":
                        contexts[match.match_key] = context
                    else:
                        existing_details = dict(getattr(existing, "details", {}) or {})
                        existing_details["bzzoiro_v2_resources"] = resources
                        existing_details["provider_odds_hints"] = list(existing_details.get("provider_odds_hints") or []) + hints
                        existing_details["provider_odds_hints_count"] = len(existing_details.get("provider_odds_hints") or [])
                        existing.details = existing_details
                    v2_stats["contexts_built"] += 1
        except Exception as exc:
            v2_stats["errors"] += 1
            v2_stats["last_error"] = f"{type(exc).__name__}: {exc}"
        stats["bzzoiro_v2"] = v2_stats
        preview["bzzoiro_v2"] = {"target_matches": len(target_matches), "events_fetched": len(events), "contexts_built": v2_stats["contexts_built"]}
        return contexts, stats, preview

    BzzoiroContextProvider._prioritize_matches = priority  # type: ignore[assignment]
    BzzoiroContextProvider.fetch_context = patched_fetch_context  # type: ignore[assignment]
    BzzoiroContextProvider._harizon_windowed_v2_patch = True


def _patch_sstats_deep_endpoints() -> None:
    try:
        from app.providers.sstats import SStatsContextProvider
    except Exception:
        return
    if getattr(SStatsContextProvider, "_harizon_deep_endpoints_patch", False):
        return
    original_fetch_context = SStatsContextProvider.fetch_context

    async def patched_fetch_context(self, matches: list[Match]):  # type: ignore[no-untyped-def]
        contexts, stats, preview = await original_fetch_context(self, matches)
        stats = dict(stats or {})
        preview = dict(preview or {})
        if not _truthy(os.getenv("SSTATS_DEEP_ENDPOINTS_ENABLED"), True):
            return contexts, stats, preview
        key = str(getattr(getattr(self, "settings", None), "sstats_api_key", "") or os.getenv("SSTATS_API_KEY") or "").strip()
        if not key or not isinstance(contexts, dict) or not contexts:
            return contexts, stats, preview
        max_req = max(0, _to_int(os.getenv("SSTATS_DEEP_REQUESTS_MAX_PER_RUN") or 45, 45))
        used = 0
        deep_stats = {"enabled": True, "requests": 0, "contexts_checked": 0, "contexts_enriched": 0, "last_games_stats": 0, "glicko": 0, "injuries": 0, "errors": 0}
        match_by_key = {m.match_key: m for m in matches or []}
        ordered_keys = [m.match_key for m in _prioritize_matches(list(match_by_key.values()), datetime.now(UTC), getattr(self, "settings", None)) if m.match_key in contexts]
        try:
            async with httpx.AsyncClient(timeout=float(getattr(getattr(self, "settings", None), "sstats_timeout_seconds", 25.0) or 25.0)) as client:
                for match_key in ordered_keys:
                    if used >= max_req:
                        break
                    context = contexts.get(match_key)
                    if not isinstance(context, MatchContext):
                        continue
                    deep_stats["contexts_checked"] += 1
                    game_id = _find_numeric_id(context.payload)
                    if not game_id:
                        continue
                    enrichment: dict[str, Any] = {"game_id": game_id}
                    endpoints = [
                        ("last_games_stats", "https://api.sstats.net/Games/last-games-stats", {"gameId": game_id, "limit": 25, "apikey": key}),
                        ("glicko", f"https://api.sstats.net/Games/glicko/{game_id}", {"apikey": key}),
                        ("injuries", "https://api.sstats.net/Games/injuries", {"gameId": game_id, "apikey": key}),
                    ]
                    for name, url, params in endpoints:
                        if used >= max_req:
                            break
                        used += 1; deep_stats["requests"] += 1
                        try:
                            response = await client.get(url, params=params)
                            if response.status_code == 200:
                                enrichment[name] = _response_json(response)
                                deep_stats[name] = int(deep_stats.get(name, 0) or 0) + 1
                            else:
                                deep_stats["errors"] += 1
                        except Exception:
                            deep_stats["errors"] += 1
                    if len(enrichment) > 1:
                        details = dict(context.details or {})
                        details["sstats_deep_endpoints"] = enrichment
                        details["sstats_deep_endpoint_count"] = len(enrichment) - 1
                        context.details = details
                        deep_stats["contexts_enriched"] += 1
        except Exception as exc:
            deep_stats["errors"] += 1
            deep_stats["last_error"] = f"{type(exc).__name__}: {exc}"
        stats["sstats_deep"] = deep_stats
        preview["sstats_deep"] = {"contexts_enriched": deep_stats["contexts_enriched"], "requests": deep_stats["requests"]}
        return contexts, stats, preview

    SStatsContextProvider.fetch_context = patched_fetch_context  # type: ignore[assignment]
    SStatsContextProvider._harizon_deep_endpoints_patch = True


def _patch_runner_targets() -> None:
    try:
        from app.services.runner import PredictionRunner
    except Exception:
        return
    if getattr(PredictionRunner, "_harizon_windowed_targets_patch", False):
        return
    original_select_context = getattr(PredictionRunner, "_select_context_enrichment_matches", None)
    original_select_provider = getattr(PredictionRunner, "_select_provider_context_matches", None)
    original_filter_matches = getattr(PredictionRunner, "_filter_matches", None)

    if callable(original_filter_matches):
        def patched_filter_matches(self, matches, now_utc):  # type: ignore[no-untyped-def]
            result = original_filter_matches(self, matches, now_utc)
            try:
                filtered, meta = result
                filtered = _prioritize_matches(list(filtered or []), now_utc, getattr(self, "settings", None))
                if isinstance(meta, dict):
                    meta["windowed_core_coverage"] = {"enabled": True, "window_hours": _window_hours(), "cron_interval_hours": _cron_interval_hours()}
                return filtered, meta
            except Exception:
                return result
        PredictionRunner._filter_matches = patched_filter_matches  # type: ignore[assignment]

    if callable(original_select_context):
        def patched_select_context(self, filtered_matches, merged_offers, now_utc, market_signals):  # type: ignore[no-untyped-def]
            result = original_select_context(self, filtered_matches, merged_offers, now_utc, market_signals)
            try:
                selected, meta = result
                cap = max(1, _to_int(os.getenv("CORE_COVERAGE_CONTEXT_TARGET_LIMIT") or getattr(self.settings, "context_enrichment_match_limit", 240), 240))
                near = _prioritize_matches(list(filtered_matches or []), now_utc, getattr(self, "settings", None), cap)
                selected = _unique_matches(near, list(selected or []))[:cap]
                if isinstance(meta, dict):
                    meta["windowed_core_coverage"] = {"selected": len(selected), "near_window_added": len(near), "window_hours": _window_hours()}
                return selected, meta
            except Exception:
                return result
        PredictionRunner._select_context_enrichment_matches = patched_select_context  # type: ignore[assignment]

    if callable(original_select_provider):
        def patched_select_provider(self, context_target_matches, provider_key, *args, **kwargs):  # type: ignore[no-untyped-def]
            result = original_select_provider(self, context_target_matches, provider_key, *args, **kwargs)
            key = _norm_provider(provider_key)
            if key not in {"sstats", "bzzoiro"}:
                return result
            try:
                fallback = list(kwargs.get("fallback_matches") or [])
                cap_env = "SSTATS_CONTEXT_MATCH_LIMIT" if key == "sstats" else "BZZOIRO_CONTEXT_MATCH_LIMIT"
                cap = max(1, _to_int(os.getenv(cap_env) or os.getenv("CORE_COVERAGE_PROVIDER_TARGET_LIMIT") or 240, 240))
                near = _prioritize_matches(_unique_matches(list(context_target_matches or []), fallback), datetime.now(UTC), getattr(self, "settings", None), cap)
                return _unique_matches(near, list(result or []))[:cap]
            except Exception:
                return result
        PredictionRunner._select_provider_context_matches = patched_select_provider  # type: ignore[assignment]

    PredictionRunner._harizon_windowed_targets_patch = True


def _build_context_coverage(contexts_by_match: Any) -> dict[str, set[str]]:
    coverage: dict[str, set[str]] = {}
    if not isinstance(contexts_by_match, dict):
        return coverage
    for match_key, context in contexts_by_match.items():
        sources = _context_sources(context)
        if sources:
            coverage[str(match_key)] = sources
    return coverage


def _patch_candidate_publication_guard() -> None:
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if getattr(CandidateFactory, "_harizon_windowed_core_guard_patch", False):
        return
    original_build_candidates = CandidateFactory.build_candidates

    def patched_build_candidates(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        candidates, rejections, debug = original_build_candidates(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=market_signals_by_match)
        rejections = dict(rejections or {})
        debug = dict(debug or {})
        if not _truthy(os.getenv("WINDOWED_CORE_COVERAGE_ENABLED"), True):
            return candidates, rejections, debug
        now = datetime.now(UTC)
        context_cov = _build_context_coverage(contexts_by_match)
        offer_cov = {str(k): _offer_sources_from_map(v) for k, v in dict(offers_by_match or {}).items()}
        min_odds = max(1, _to_int(os.getenv("CORE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2))
        min_context = max(1, _to_int(os.getenv("CORE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2))
        min_core = max(1, _to_int(os.getenv("CORE_COVERAGE_MIN_CORE_PROVIDERS") or 2, 2))
        kept = []
        rejected_rows = []
        for candidate in list(candidates or []):
            match_key = str(getattr(candidate, "match_key", "") or "")
            odds_sources = _offer_sources_from_candidate(candidate) | offer_cov.get(match_key, set())
            context_sources = set(context_cov.get(match_key, set()))
            summary = dict(getattr(candidate, "source_summary", {}) or {})
            context_sources |= _provider_tokens_from_payload(summary.get("context_source"))
            context_sources |= _provider_tokens_from_payload(summary.get("context_sources"))
            if summary.get("context_source") == "ensemble" and float((getattr(candidate, "analysis", {}) or {}).get("context_depth_score") or 0.0) >= 2.0:
                context_sources |= _provider_tokens_from_payload(getattr(candidate, "analysis", {}) or {})
            core_count = len((odds_sources | context_sources) & CORE_PROVIDERS)
            movement = _movement_status(candidate, now, getattr(self, "settings", None))
            reject_reasons: list[str] = []
            if len(odds_sources) < min_odds:
                reject_reasons.append("odds_sources_below_2")
            if len(context_sources) < min_context:
                reject_reasons.append("context_sources_below_2")
            if core_count < min_core:
                reject_reasons.append("core_api_coverage_below_2_of_3")
            if not movement.get("ok"):
                reject_reasons.append(str(movement.get("reason") or "line_movement_not_confirmed"))
            if reject_reasons:
                rejections["windowed_core_coverage_guard"] = int(rejections.get("windowed_core_coverage_guard", 0) or 0) + 1
                try:
                    candidate.reasons.append("windowed_core_coverage_guard=" + "+".join(reject_reasons))
                    candidate.source_summary["windowed_core_coverage"] = {
                        "accepted": False,
                        "reject_reasons": reject_reasons,
                        "odds_sources": sorted(odds_sources),
                        "context_sources": sorted(context_sources),
                        "core_provider_count": core_count,
                        "movement": movement,
                    }
                except Exception:
                    pass
                rejected_rows.append({
                    "match_key": match_key,
                    "home": getattr(candidate, "home_team", ""),
                    "away": getattr(candidate, "away_team", ""),
                    "kickoff": getattr(getattr(candidate, "commence_time", None), "isoformat", lambda: "")(),
                    "family": getattr(candidate, "family", ""),
                    "selection": getattr(candidate, "selection", ""),
                    "reject_reasons": reject_reasons,
                    "odds_sources": sorted(odds_sources),
                    "context_sources": sorted(context_sources),
                    "movement": movement,
                })
                continue
            try:
                candidate.reasons.append("windowed_core_coverage=2plus_odds_2plus_context")
                candidate.source_summary["windowed_core_coverage"] = {
                    "accepted": True,
                    "odds_sources": sorted(odds_sources),
                    "context_sources": sorted(context_sources),
                    "core_provider_count": core_count,
                    "movement": movement,
                    "window_index": _window_index(candidate, now, getattr(self, "settings", None)),
                }
            except Exception:
                pass
            kept.append(candidate)
        report = {
            "created_at_utc": now.isoformat(),
            "enabled": True,
            "window_hours": _window_hours(),
            "cron_interval_hours": _cron_interval_hours(),
            "next_cron_utc": _next_cron_time(now, getattr(self, "settings", None)).isoformat(),
            "candidates_in": len(candidates or []),
            "candidates_kept": len(kept),
            "candidates_rejected": len(rejected_rows),
            "min_odds_sources": min_odds,
            "min_context_sources": min_context,
            "min_core_providers": min_core,
            "rejected_sample": rejected_rows[:30],
            "context_coverage_matches": len(context_cov),
            "offer_coverage_matches": len(offer_cov),
        }
        debug["windowed_core_coverage_guard"] = report
        _write_report(report)
        return kept, rejections, debug

    CandidateFactory.build_candidates = patched_build_candidates  # type: ignore[assignment]
    CandidateFactory._harizon_windowed_core_guard_patch = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _apply_env_defaults()
    _patch_odds_api_io_priority()
    _patch_bzzoiro_v2()
    _patch_sstats_deep_endpoints()
    _patch_runner_targets()
    _patch_candidate_publication_guard()
    _INSTALLED = True
    _write_report({
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": True,
        "status": "installed",
        "window_hours": _window_hours(),
        "cron_interval_hours": _cron_interval_hours(),
        "core_providers": sorted(CORE_PROVIDERS),
        "publication_guard": "2plus_odds_2plus_context_plus_movement_window",
    })
    return {"status": "installed", "patch": "windowed_core_coverage_runtime_patch"}
