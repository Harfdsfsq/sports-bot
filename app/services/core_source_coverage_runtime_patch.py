from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

UTC = timezone.utc

_INSTALLED = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _set_default(name: str, value: str) -> None:
    if os.getenv(name) in (None, ""):
        os.environ[name] = str(value)


def _merge_stat_blocks(primary: dict[str, Any], secondary: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    merged = dict(primary or {})
    merged[f"{prefix}_stats"] = dict(secondary or {})
    for key, value in (secondary or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[f"{prefix}_{key}"] = value
    return merged


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _extract_next_cursor(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("pagination", "meta"):
        obj = payload.get(key)
        if isinstance(obj, dict):
            cursor = obj.get("next_cursor") or obj.get("nextCursor") or obj.get("cursor")
            if cursor:
                return str(cursor)
            has_more = obj.get("has_more")
            if has_more is False:
                return None
    cursor = payload.get("next_cursor") or payload.get("nextCursor")
    if cursor:
        return str(cursor)
    return None


async def _sportlogic_fetch_games_window(self: Any, client: httpx.AsyncClient, date_from: str, date_to: str, stats: dict[str, Any], preview: dict[str, Any], *, status: str | None = "scheduled") -> list[dict[str, Any]]:
    """Fetch SportLogic games with the documented date_from/date_to + cursor contract."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = None
    page = 0
    per_page = min(100, max(1, _safe_int(os.getenv("SPORTLOGIC_PER_PAGE") or getattr(self, "sportlogic_per_page", 100), 100)))
    max_pages = max(1, _safe_int(os.getenv("SPORTLOGIC_MAX_GAME_PAGES_PER_RUN") or 8, 8))

    while page < max_pages:
        if not self._budget_left():
            stats["budget_exhausted"] = True
            break
        params: dict[str, Any] = {"date_from": date_from, "date_to": date_to, "per_page": per_page}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        payload = await self._get_json(client, "/games", params, stats, preview)
        batch = self._extract_list(payload)
        stats.setdefault("game_page_rows", []).append(len(batch))
        if not batch:
            break
        for row in batch:
            sig = str(row.get("id") or row.get("game_id") or row.get("uuid") or row)
            if sig in seen:
                continue
            seen.add(sig)
            rows.append(row)
        cursor = _extract_next_cursor(payload)
        page += 1
        if not cursor or len(batch) < per_page:
            break
    stats["game_pages_requested"] = int(stats.get("game_pages_requested", 0) or 0) + page
    return rows


async def _sportlogic_load_fixtures_for_matches(self: Any, matches: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    stats = self._stats("fixtures")
    preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}
    if not matches:
        return [], stats, preview
    dates = [m.commence_time.astimezone(UTC).date() for m in matches]
    date_from = min(dates).isoformat()
    date_to = max(dates).isoformat()
    fixtures: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
        fixtures.extend(await _sportlogic_fetch_games_window(self, client, date_from, date_to, stats, preview, status="scheduled"))
        # Some SportLogic deployments keep imminent matches as live or omit status in filters.
        # Fallback only if the scheduled page is suspiciously thin.
        if len(fixtures) < max(8, min(len(matches), 30) // 3) and self._budget_left():
            stats["fallback_unscoped_games_query"] = True
            fixtures.extend(await _sportlogic_fetch_games_window(self, client, date_from, date_to, stats, preview, status=None))
    # Deduplicate after scheduled + fallback merge.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in fixtures:
        sig = str(row.get("id") or row.get("game_id") or row.get("uuid") or row)
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(row)
    self._fixture_cache = deduped
    stats["fixtures_fetched"] = len(deduped)
    preview["sample_fixtures"] = deduped[:3]
    return deduped, stats, preview


async def _sportlogic_fetch_matches(self: Any) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    stats = self._stats("matches")
    preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": []}
    if not self._ready(stats):
        return [], stats, preview
    now = datetime.now(UTC)
    days_ahead = max(1, _safe_int(getattr(self.settings, "run_days_ahead", 1) or 1, 1))
    date_from = now.date().isoformat()
    date_to = (now + timedelta(days=days_ahead)).date().isoformat()
    fixtures: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
        fixtures.extend(await _sportlogic_fetch_games_window(self, client, date_from, date_to, stats, preview, status="scheduled"))
        if len(fixtures) < 20 and self._budget_left():
            stats["fallback_unscoped_games_query"] = True
            fixtures.extend(await _sportlogic_fetch_games_window(self, client, date_from, date_to, stats, preview, status=None))
    deduped_rows: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    for row in fixtures:
        sig = str(row.get("id") or row.get("game_id") or row.get("uuid") or row)
        if sig in seen_rows:
            continue
        seen_rows.add(sig)
        deduped_rows.append(row)
    self._fixture_cache = deduped_rows
    stats["fixtures_fetched"] = len(deduped_rows)
    preview["sample_fixtures"] = deduped_rows[:3]

    horizon = now + timedelta(days=days_ahead, hours=2)
    matches: list[Any] = []
    seen_matches: set[str] = set()
    for row in deduped_rows:
        match = self._row_to_match(row)
        if match is None:
            stats["fixtures_skipped"] += 1
            continue
        commence = match.commence_time.astimezone(UTC)
        if commence < now - timedelta(minutes=90) or commence > horizon:
            stats["fixtures_outside_window"] = int(stats.get("fixtures_outside_window", 0) or 0) + 1
            continue
        if match.match_key in seen_matches:
            continue
        seen_matches.add(match.match_key)
        matches.append(match)
    matches = self._prioritize_matches(matches)[: self.match_limit]
    stats["matches_built"] = len(matches)
    preview["sample_matches"] = [
        {
            "match_key": m.match_key,
            "league_name": m.league_name,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "commence_time": m.commence_time.isoformat(),
        }
        for m in matches[:8]
    ]
    return matches, stats, preview


def _sportlogic_bookmaker(row: dict[str, Any]) -> str:
    bookmaker = row.get("bookmaker") or row.get("book") or row.get("sportsbook") or row.get("provider")
    if isinstance(bookmaker, dict):
        return str(bookmaker.get("name") or bookmaker.get("title") or bookmaker.get("slug") or "SportLogic")
    return str(bookmaker or row.get("bookmaker_name") or "SportLogic")


def _sportlogic_market_key(row: dict[str, Any]) -> str:
    market = row.get("market") or row.get("market_key") or row.get("market_name") or row.get("type")
    if isinstance(market, dict):
        return str(market.get("key") or market.get("name") or market.get("id") or "")
    return str(market or "")


def _sportlogic_market_id(row: dict[str, Any]) -> str:
    market = row.get("market")
    if isinstance(market, dict):
        return str(market.get("id") or "")
    return str(row.get("market_id") or row.get("marketId") or "")


def _sportlogic_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _sportlogic_flatten_odds_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Documented SportLogic shape is already flat.
        if any(k in row for k in ("option_name", "market_id", "odds", "bookmaker")):
            out.append(row)
            continue
        bookmakers = row.get("bookmakers")
        if isinstance(bookmakers, list):
            for book in bookmakers:
                if not isinstance(book, dict):
                    continue
                book_name = _sportlogic_bookmaker(book)
                markets = book.get("markets") or book.get("odds") or book.get("data") or []
                if isinstance(markets, dict):
                    markets = list(markets.values())
                if not isinstance(markets, list):
                    continue
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    outcomes = market.get("outcomes") or market.get("selections") or market.get("options") or market.get("odds") or []
                    if isinstance(outcomes, dict):
                        outcomes = [{"option_name": k, "odds": v} for k, v in outcomes.items()]
                    for outcome in outcomes if isinstance(outcomes, list) else []:
                        if isinstance(outcome, dict):
                            merged = dict(outcome)
                            merged.setdefault("bookmaker", {"name": book_name})
                            merged.setdefault("market", market)
                            out.append(merged)
        else:
            out.append(row)
    return out


def _sportlogic_custom_parse_odds(self: Any, rows: list[dict[str, Any]], match: Any, event_id: str, stats: dict[str, Any] | None = None) -> list[Any]:
    from app.schemas import Offer
    from app.utils import normalize_bookmaker_name

    offers: list[Any] = []
    seen: set[tuple[str, str, str, float | None]] = set()
    raw_rows = _sportlogic_flatten_odds_rows(rows)

    def reject(reason: str) -> None:
        if stats is None:
            return
        reasons = stats.setdefault("parse_reject_reasons", {})
        if isinstance(reasons, dict):
            reasons[reason] = int(reasons.get(reason) or 0) + 1

    configured_books = getattr(self.settings, "sportlogic_bookmakers", None)
    if configured_books is None:
        configured_books = os.getenv("SPORTLOGIC_BOOKMAKERS", "")
    if isinstance(configured_books, str):
        allowed = {normalize_bookmaker_name(x.strip()) for x in configured_books.split(",") if x.strip()}
    else:
        allowed = {normalize_bookmaker_name(str(x).strip()) for x in (configured_books or []) if str(x).strip()}
    allowed.discard("")

    def add(book: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, market_name: str = "") -> None:
        odds = _sportlogic_float(price)
        if odds is None or odds <= 1.0:
            reject("missing_or_invalid_price")
            return
        bookmaker = normalize_bookmaker_name(book) or str(book or "sportlogic").strip().lower() or "sportlogic"
        if allowed and bookmaker not in allowed:
            reject("bookmaker_not_allowed")
            return
        key = (bookmaker, family, selection, point)
        if key in seen:
            reject("duplicate_offer")
            return
        seen.add(key)
        offers.append(Offer(
            source="sportlogic",
            bookmaker=bookmaker,
            family=family,  # type: ignore[arg-type]
            selection=selection,
            price=float(odds),
            point=point,
            team_side=team_side,
            market_name=market_name or family,
            market_key=family,
            source_event_id=event_id,
            metadata={"sportlogic_event_id": event_id, "provider_source": "sportlogic"},
        ))

    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        book = _sportlogic_bookmaker(row)
        market_key = _sportlogic_market_key(row).lower()
        market_id = _sportlogic_market_id(row)
        option = str(row.get("option_name") or row.get("selection") or row.get("outcome") or row.get("name") or row.get("label") or "").strip()
        option_low = option.lower()
        option_value = row.get("option_value")
        price = row.get("odds") or row.get("price") or row.get("decimal_odds") or row.get("decimal") or row.get("value")
        market_text = f"{market_key} {market_id}".lower()

        if not option and price in (None, ""):
            reject("missing_option_and_price")
            continue

        if market_id == "1" or any(token in market_text for token in ("match_winner", "winner", "1x2", "h2h")):
            if option_low in {"home", "1", "home_team"}:
                add(book, "h2h", match.home_team, price, team_side="home", market_name=market_key or "match_winner")
            elif option_low in {"draw", "x", "tie"}:
                add(book, "h2h", "Draw", price, market_name=market_key or "match_winner")
            elif option_low in {"away", "2", "away_team"}:
                add(book, "h2h", match.away_team, price, team_side="away", market_name=market_key or "match_winner")
            else:
                reject("unknown_h2h_selection")
            continue

        if any(token in market_text for token in ("goals_over_under", "over_under", "total", "totals", "goals")) or option_low in {"over", "under"}:
            point = _sportlogic_float(option_value or row.get("line") or row.get("point") or row.get("total"))
            if point is None:
                import re
                match_line = re.search(r"(\d+(?:[\.,]\d+)?)", f"{option} {market_key}")
                point = _sportlogic_float(match_line.group(1)) if match_line else None
            if "over" in option_low or option_low == "o":
                add(book, "totals", "Over", price, point, market_name=market_key or "goals_over_under")
            elif "under" in option_low or option_low == "u":
                add(book, "totals", "Under", price, point, market_name=market_key or "goals_over_under")
            else:
                reject("unknown_total_selection")
            continue

        if any(token in market_text for token in ("both_teams_to_score", "btts")):
            if option_low in {"yes", "y"}:
                add(book, "btts", "Yes", price, market_name=market_key or "btts")
            elif option_low in {"no", "n"}:
                add(book, "btts", "No", price, market_name=market_key or "btts")
            else:
                reject("unknown_btts_selection")
            continue

        if any(token in market_text for token in ("handicap", "spread", "asian")):
            point = _sportlogic_float(option_value or row.get("handicap") or row.get("line") or row.get("point"))
            if option_low in {"home", "1", "home_team"}:
                add(book, "spreads", match.home_team, price, point, team_side="home", market_name=market_key or "spread")
            elif option_low in {"away", "2", "away_team"}:
                add(book, "spreads", match.away_team, price, point, team_side="away", market_name=market_key or "spread")
            else:
                reject("unknown_spread_selection")
            continue

        reject("unsupported_market")

    original = getattr(self.__class__, "_harizon_original_parse_odds", None)
    if not offers and callable(original):
        try:
            return original(self, rows, match, event_id, stats)
        except Exception as exc:
            if stats is not None:
                stats["original_parse_error"] = f"{type(exc).__name__}: {exc}"
    return offers


def _patch_sportlogic() -> None:
    try:
        from app.providers.sportlogic_provider import SportLogicProvider
    except Exception:
        return
    if getattr(SportLogicProvider, "_harizon_core_patch_installed", False):
        return
    SportLogicProvider._harizon_original_fetch_matches = getattr(SportLogicProvider, "fetch_matches", None)
    SportLogicProvider._harizon_original_load_fixtures_for_matches = getattr(SportLogicProvider, "_load_fixtures_for_matches", None)
    SportLogicProvider._harizon_original_parse_odds = getattr(SportLogicProvider, "_parse_odds", None)
    SportLogicProvider.fetch_matches = _sportlogic_fetch_matches
    SportLogicProvider._load_fixtures_for_matches = _sportlogic_load_fixtures_for_matches
    SportLogicProvider._parse_odds = _sportlogic_custom_parse_odds
    SportLogicProvider._harizon_core_patch_installed = True


async def _bzzoiro_fetch_context(self: Any, matches: list[Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stats: dict[str, Any] = {
        "enabled": bool(getattr(self, "api_key", None)),
        "api_key_present": bool(getattr(self, "api_key", None)),
        "runtime_patch": "bzzoiro_v2_first_then_legacy_gapfill",
        "contexts_built": 0,
        "v2_contexts_built": 0,
        "legacy_contexts_built": 0,
        "legacy_gapfill_enabled": _env_bool("BZZOIRO_LEGACY_GAPFILL_ENABLED", True),
    }
    preview: dict[str, Any] = {"v2": {}, "legacy": {}, "merged_examples": []}
    if not getattr(self, "api_key", None) or not matches:
        return {}, stats, preview

    contexts: dict[str, Any] = {}
    v2_stats: dict[str, Any] = {}
    v2_preview: dict[str, Any] = {}
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider as BzzoiroV2Provider
        v2 = BzzoiroV2Provider(self.settings)
        # Coverage mode: Bzzoiro is documented as no hard daily quota, but keep a soft request cap via env.
        v2.max_events = max(0, _safe_int(os.getenv("BZZOIRO_V2_MAX_EVENTS") or 0, 0))
        v2.fetch_event_odds = _env_bool("BZZOIRO_V2_FETCH_EVENT_ODDS", True)
        v2.fetch_event_stats = _env_bool("BZZOIRO_V2_FETCH_EVENT_STATS", True)
        v2.fetch_event_metadata = _env_bool("BZZOIRO_V2_FETCH_EVENT_METADATA", False)
        v2_contexts, v2_stats, v2_preview = await v2.fetch_context(matches)
        contexts.update(v2_contexts or {})
        stats["v2_contexts_built"] = len(v2_contexts or {})
        stats = _merge_stat_blocks(stats, v2_stats, prefix="v2")
        preview["v2"] = v2_preview
    except Exception as exc:
        stats["v2_error"] = f"{type(exc).__name__}: {exc}"

    original = getattr(self.__class__, "_harizon_original_fetch_context", None)
    missing_count = len([m for m in matches if getattr(m, "sport_key", "") == "soccer" and m.match_key not in contexts])
    should_gapfill = bool(original) and stats["legacy_gapfill_enabled"] and missing_count > 0
    if should_gapfill:
        try:
            legacy_contexts, legacy_stats, legacy_preview = await original(self, matches)
            stats["legacy_contexts_built"] = len(legacy_contexts or {})
            stats = _merge_stat_blocks(stats, legacy_stats, prefix="legacy")
            preview["legacy"] = legacy_preview
            for key, ctx in (legacy_contexts or {}).items():
                if key not in contexts:
                    contexts[key] = ctx
        except Exception as exc:
            stats["legacy_error"] = f"{type(exc).__name__}: {exc}"
    stats["contexts_built"] = len(contexts)
    for key, ctx in list(contexts.items())[:8]:
        preview["merged_examples"].append({
            "match_key": key,
            "source": getattr(ctx, "source", ""),
            "expected_home": getattr(ctx, "expected_home", None),
            "expected_away": getattr(ctx, "expected_away", None),
            "confidence": getattr(ctx, "confidence", None),
        })
    return contexts, stats, preview


def _patch_bzzoiro() -> None:
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception:
        return
    if getattr(BzzoiroContextProvider, "_harizon_core_patch_installed", False):
        return
    BzzoiroContextProvider._harizon_original_fetch_context = getattr(BzzoiroContextProvider, "fetch_context", None)
    BzzoiroContextProvider.fetch_context = _bzzoiro_fetch_context
    BzzoiroContextProvider._harizon_core_patch_installed = True


async def _sstats_fetch_context(self: Any, matches: list[Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stats: dict[str, Any] = {
        "enabled": bool(getattr(self.settings, "enable_sstats_context", True)),
        "api_key_present": bool(getattr(self.settings, "sstats_api_key", None)),
        "runtime_patch": "sstats_v1_first_then_legacy_gapfill",
        "contexts_built": 0,
        "v1_contexts_built": 0,
        "legacy_contexts_built": 0,
        "legacy_gapfill_enabled": _env_bool("SSTATS_LEGACY_GAPFILL_ENABLED", False),
    }
    preview: dict[str, Any] = {"v1": {}, "legacy": {}, "merged_examples": []}
    if not stats["enabled"] or not stats["api_key_present"] or not matches:
        return {}, stats, preview
    contexts: dict[str, Any] = {}
    try:
        from app.providers.sstats_v1 import SStatsContextProvider as SStatsV1Provider
        v1 = SStatsV1Provider(self.settings)
        v1_contexts, v1_stats, v1_preview = await v1.fetch_context(matches)
        contexts.update(v1_contexts or {})
        stats["v1_contexts_built"] = len(v1_contexts or {})
        stats = _merge_stat_blocks(stats, v1_stats, prefix="v1")
        preview["v1"] = v1_preview
    except Exception as exc:
        stats["v1_error"] = f"{type(exc).__name__}: {exc}"

    original = getattr(self.__class__, "_harizon_original_fetch_context", None)
    missing_count = len([m for m in matches if getattr(m, "sport_key", "") == "soccer" and m.match_key not in contexts])
    if callable(original) and stats["legacy_gapfill_enabled"] and missing_count > 0:
        try:
            legacy_contexts, legacy_stats, legacy_preview = await original(self, matches)
            stats["legacy_contexts_built"] = len(legacy_contexts or {})
            stats = _merge_stat_blocks(stats, legacy_stats, prefix="legacy")
            preview["legacy"] = legacy_preview
            for key, ctx in (legacy_contexts or {}).items():
                if key not in contexts:
                    contexts[key] = ctx
        except Exception as exc:
            stats["legacy_error"] = f"{type(exc).__name__}: {exc}"
    stats["contexts_built"] = len(contexts)
    for key, ctx in list(contexts.items())[:8]:
        preview["merged_examples"].append({
            "match_key": key,
            "source": getattr(ctx, "source", ""),
            "expected_home": getattr(ctx, "expected_home", None),
            "expected_away": getattr(ctx, "expected_away", None),
            "confidence": getattr(ctx, "confidence", None),
        })
    return contexts, stats, preview


def _patch_sstats() -> None:
    try:
        from app.providers.sstats import SStatsContextProvider
    except Exception:
        return
    if getattr(SStatsContextProvider, "_harizon_core_patch_installed", False):
        return
    SStatsContextProvider._harizon_original_fetch_context = getattr(SStatsContextProvider, "fetch_context", None)
    SStatsContextProvider.fetch_context = _sstats_fetch_context
    SStatsContextProvider._harizon_core_patch_installed = True


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _set_default("SPORTLOGIC_PER_PAGE", "100")
    _set_default("SPORTLOGIC_MAX_GAME_PAGES_PER_RUN", "8")
    _set_default("SPORTLOGIC_ODDS_MATCH_LIMIT", "45")
    _set_default("BZZOIRO_V2_PAGE_SIZE", "200")
    _set_default("BZZOIRO_V2_FETCH_EVENT_ODDS", "true")
    _set_default("BZZOIRO_V2_FETCH_EVENT_STATS", "true")
    _set_default("BZZOIRO_V2_FETCH_EVENT_METADATA", "false")
    _set_default("BZZOIRO_LEGACY_GAPFILL_ENABLED", "true")
    _set_default("SSTATS_LEGACY_GAPFILL_ENABLED", "false")
    _set_default("SSTATS_DETAIL_MATCH_LIMIT", "45")
    _patch_sportlogic()
    _patch_bzzoiro()
    _patch_sstats()
