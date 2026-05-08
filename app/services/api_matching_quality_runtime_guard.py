from __future__ import annotations

"""Runtime guard for API matching, request quality and exact price integrity."""

import os
import re
from typing import Any

PATCH_MARKER = "_harizon_api_matching_quality_guard_v2"

CONTEXT_ONLY_SOURCES = {
    "sstats", "clubelo", "football_data", "football_data_org", "thesportsdb", "openligadb", "openfootball",
    "weather", "weatherapi", "openweathermap", "meteostat", "newsapi", "currents", "gnews", "newsdata",
    "guardian", "self_history", "futrixmetrics", "bzzoiro",
}

PRICE_SOURCE_ALIASES = {
    "odds_api_io": "odds_api_io",
    "odds-api.io": "odds_api_io",
    "oddsapiio": "odds_api_io",
    "bzzoiro_event_odds": "bzzoiro",
    "bzzoiro_odds": "bzzoiro",
    "bzzoiro": "bzzoiro",
    "allsportsapi": "allsportsapi",
    "all_sports_api": "allsportsapi",
    "sportlogic": "sportlogic",
    "sportsbook_api": "sportsbook_api",
    "oddsfeed": "oddsfeed",
    "odds_feed": "oddsfeed",
    "highlightly": "highlightly",
}

NON_FULL_TIME_MARKET_RE = re.compile(
    r"\b("
    r"ht|1h|2h|1st\s*half|2nd\s*half|first\s*half|second\s*half|half\s*time|"
    r"corners?|cards?|bookings?|offsides?|throw\s*ins?|shots?|saves?|player|"
    r"penalt(?:y|ies)|free\s*kicks?|goal\s*kicks?|period|quarter|set|map|"
    r"перв(?:ый|ом)\s*тайм|втор(?:ой|ом)\s*тайм|тайм|углов|карточ"
    r")\b",
    re.IGNORECASE,
)


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9а-я_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _canonical_price_source(value: Any) -> str | None:
    key = _norm(value)
    if not key or key in CONTEXT_ONLY_SOURCES:
        return None
    if key in PRICE_SOURCE_ALIASES:
        return PRICE_SOURCE_ALIASES[key]
    for needle, canonical in PRICE_SOURCE_ALIASES.items():
        if needle and needle in key:
            return canonical
    return key


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _offer_dicts(candidate: Any) -> list[Any]:
    raw = getattr(candidate, "raw_bucket_offers", None)
    if isinstance(raw, list) and raw:
        return raw
    for container_name in ("source_summary", "diagnostics"):
        container = getattr(candidate, container_name, None)
        if isinstance(container, dict):
            for key in ("offers", "bucket_offers", "selected_offers", "raw_bucket_offers"):
                value = container.get(key)
                if isinstance(value, list) and value:
                    return value
    return []


def _line_side_from_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("over", "больше", "тб")):
        return "over"
    if any(token in text for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in text for token in ("yes", "да")):
        return "yes"
    if any(token in text for token in ("no", "нет")):
        return "no"
    if "home" in text:
        return "home"
    if "away" in text:
        return "away"
    return _norm(text)


def _candidate_side(candidate: Any) -> str:
    return _line_side_from_text(" ".join(str(getattr(candidate, attr, "") or "") for attr in ("selection", "selection_key", "team_side")))


def _offer_text(offer: Any) -> str:
    fields = ("selection", "selection_key", "team_side", "name", "label", "market_name", "market_key", "market_subtype")
    return " ".join(str(_field(offer, key, "") or "") for key in fields).strip()


def _is_full_time_market_name(market_name: Any) -> bool:
    text = str(market_name or "").strip()
    if not text:
        return True
    return NON_FULL_TIME_MARKET_RE.search(text) is None


def _same_exact_line(candidate: Any, offer: Any) -> bool:
    cand_family = _norm(getattr(candidate, "family", ""))
    offer_family = _norm(_field(offer, "family") or _field(offer, "market_key") or _field(offer, "market_name"))
    if cand_family and offer_family and cand_family != offer_family:
        if not (cand_family == "totals" and "total" in offer_family):
            return False
    if not _is_full_time_market_name(_field(offer, "market_name") or _field(offer, "market_key")):
        return False
    cand_point = getattr(candidate, "point", None)
    offer_point = _field(offer, "point") or _field(offer, "line") or _field(offer, "handicap") or _field(offer, "hdp")
    if cand_point not in (None, "") or offer_point not in (None, ""):
        cp = _as_float(cand_point, None)
        op = _as_float(offer_point, None)
        if cp is None or op is None or abs(cp - op) > 1e-9:
            return False
    side = _candidate_side(candidate)
    text = _offer_text(offer).lower()
    if side == "over" and not any(token in text for token in ("over", "больше", "тб")):
        return False
    if side == "under" and not any(token in text for token in ("under", "меньше", "тм")):
        return False
    if side == "yes" and not any(token in text for token in ("yes", "да")):
        return False
    if side == "no" and not any(token in text for token in ("no", "нет")):
        return False
    return True


def _exact_price_inventory(candidate: Any) -> dict[str, Any]:
    price_sources: set[str] = set()
    books: set[str] = set()
    prices: list[float] = []
    market_names: set[str] = set()
    exact_offers = 0
    for offer in _offer_dicts(candidate):
        if not _same_exact_line(candidate, offer):
            continue
        exact_offers += 1
        source = _canonical_price_source(_field(offer, "source"))
        if source:
            price_sources.add(source)
        metadata = _field(offer, "metadata")
        if isinstance(metadata, dict):
            source = _canonical_price_source(metadata.get("source") or metadata.get("provider"))
            if source:
                price_sources.add(source)
        book = _norm(_field(offer, "bookmaker") or _field(offer, "book") or _field(offer, "site"))
        if book:
            books.add(book)
        price = _as_float(_field(offer, "price") or _field(offer, "odds") or _field(offer, "decimal"), None)
        if price and price > 1.0:
            prices.append(price)
        market_name = str(_field(offer, "market_name") or _field(offer, "market_key") or "").strip()
        if market_name:
            market_names.add(market_name)
    summary = getattr(candidate, "source_summary", None)
    if isinstance(summary, dict):
        for field in ("price_sources", "price_source_names", "odds_sources", "odds_source_names", "selected_odds_sources"):
            value = summary.get(field)
            values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;/|]+", str(value or ""))
            for item in values:
                source = _canonical_price_source(item)
                if source:
                    price_sources.add(source)
        selected = _canonical_price_source(summary.get("selected_source") or summary.get("source"))
        if selected:
            price_sources.add(selected)
    if not price_sources:
        source = _canonical_price_source(getattr(candidate, "source", None))
        if source:
            price_sources.add(source)
    selected_book = _norm(getattr(candidate, "bookmaker", None))
    if selected_book:
        books.add(selected_book)
    report = {
        "exact_price_sources_count": len(price_sources),
        "exact_price_sources": sorted(price_sources),
        "exact_line_bookmakers_count": len(books),
        "exact_line_bookmakers": sorted(books),
        "exact_line_offers_count": exact_offers,
        "exact_line_prices": sorted(set(round(p, 4) for p in prices)),
        "exact_line_market_names": sorted(market_names),
    }
    try:
        if not isinstance(candidate.source_summary, dict):
            candidate.source_summary = {}
        candidate.source_summary.update(report)
        candidate.source_summary["price_sources_count"] = len(price_sources)
        candidate.source_summary["odds_sources_count"] = len(price_sources)
    except Exception:
        pass
    try:
        candidate.integrity_report = {**dict(getattr(candidate, "integrity_report", {}) or {}), **report}
    except Exception:
        pass
    return report


def _candidate_price(candidate: Any) -> float:
    values: list[float] = []
    for attr in ("odds", "selected_odds", "price_used_for_ev"):
        value = _as_float(getattr(candidate, attr, None), None)
        if value and value > 1.0:
            values.append(value)
    inv = _exact_price_inventory(candidate)
    for value in inv.get("exact_line_prices") or []:
        price = _as_float(value, None)
        if price and price > 1.0:
            values.append(price)
    return max(values) if values else 0.0


def _patch_market_integrity() -> bool:
    try:
        from app.services import market_integrity as mi
    except Exception:
        return False
    if getattr(mi, PATCH_MARKER, False):
        return False
    original_sources_count = getattr(mi, "_sources_count", None)
    original_books_count = getattr(mi, "_books_count", None)
    original_validate = getattr(mi, "validate_candidate", None)
    decision_cls = getattr(mi, "IntegrityDecision", None)

    def sources_count_patched(candidate: Any) -> int:
        exact = int(_exact_price_inventory(candidate).get("exact_price_sources_count") or 0)
        if exact > 0:
            return exact
        if callable(original_sources_count):
            return int(original_sources_count(candidate) or 0)
        return 0

    def books_count_patched(candidate: Any) -> int:
        exact = int(_exact_price_inventory(candidate).get("exact_line_bookmakers_count") or 0)
        if exact > 0:
            return exact
        if callable(original_books_count):
            return int(original_books_count(candidate) or 0)
        return 0

    def validate_candidate_patched(candidate: Any):
        if callable(original_validate):
            decision = original_validate(candidate)
            reasons = list(getattr(decision, "reasons", []) or [])
            report = dict(getattr(decision, "report", {}) or {})
        else:
            reasons = []
            report = {}
        inventory = _exact_price_inventory(candidate)
        report.update(inventory)
        family = _norm(getattr(candidate, "family", ""))
        point = _as_float(getattr(candidate, "point", None), None)
        price = _candidate_price(candidate)
        side = _candidate_side(candidate)
        exact_sources = int(inventory.get("exact_price_sources_count") or 0)
        exact_books = int(inventory.get("exact_line_bookmakers_count") or 0)
        if family in {"totals", "spreads"}:
            bad_markets = [name for name in inventory.get("exact_line_market_names", []) if not _is_full_time_market_name(name)]
            if bad_markets:
                reasons.append("non_full_time_or_prop_market:" + ",".join(bad_markets[:3]))
        if family == "totals" and side == "over" and point is not None and point <= 1.5:
            max_reasonable = _as_float(os.getenv("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS"), 1.65) or 1.65
            min_books = max(2, _as_int(os.getenv("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS"), 3))
            if price > max_reasonable and (exact_sources < 2 or exact_books < min_books):
                reasons.append(f"suspicious_total_over_1_5_exact_guard:odds={price:.2f},max={max_reasonable:.2f},price_sources={exact_sources},books={exact_books}/{min_books}")
        deduped: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            key = str(reason)
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        if decision_cls is not None:
            return decision_cls(passed=not deduped, reasons=deduped, report=report)
        return type("IntegrityDecisionCompat", (), {"passed": not deduped, "reasons": deduped, "report": report})()

    mi._sources_count = sources_count_patched
    mi._books_count = books_count_patched
    mi.validate_candidate = validate_candidate_patched
    setattr(mi, PATCH_MARKER, True)
    return True


def _call_supported_market_helper(helper: Any, self_obj: Any, market_key: Any) -> bool:
    if not callable(helper):
        return True
    attempts = (
        (market_key,),
        (self_obj, market_key),
    )
    last_type_error: TypeError | None = None
    for args in attempts:
        try:
            return bool(helper(*args))
        except TypeError as exc:
            last_type_error = exc
            continue
    if last_type_error is not None:
        raise last_type_error
    return True


def _patch_odds_api_io_provider() -> bool:
    try:
        from app.providers import odds_api_io as module
        from app.utils import league_similarity, score_event_match
    except Exception:
        return False
    cls = getattr(module, "OddsApiIoProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False
    original_is_supported = getattr(cls, "_is_supported_market", None)
    if callable(original_is_supported):
        def is_supported_market_patched(*args: Any, **kwargs: Any) -> bool:
            if len(args) >= 2:
                self_obj = args[0]
                market_key = args[1]
            elif len(args) == 1:
                self_obj = None
                market_key = args[0]
            else:
                self_obj = kwargs.get("self")
                market_key = kwargs.get("market_key")
            text = str(market_key or "")
            if not _is_full_time_market_name(text):
                return False
            return _call_supported_market_helper(original_is_supported, self_obj, market_key)
        cls._is_supported_market = is_supported_market_patched

    def match_event_patched(self: Any, event: dict[str, Any], matches: list[Any]) -> Any | None:
        best_match = None
        best_score = 0.0
        best_quality = None
        second_score = 0.0
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        for match in matches or []:
            score, quality = score_event_match(
                sport=match.sport_key,
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=event.get("home", ""),
                event_away=event.get("away", ""),
                event_start=event.get("commence_time"),
                event_league=event.get("league", ""),
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if quality == "fuzzy":
                try:
                    lg = league_similarity(match.league_name, event.get("league", ""))
                except Exception:
                    lg = 0.0
                if lg < 0.35:
                    score *= 0.88
            if score > best_score:
                second_score = best_score
                best_score = score
                best_quality = quality
                best_match = match
            elif score > second_score:
                second_score = score
        min_score = float(os.getenv("ODDS_API_IO_MATCH_MIN_SCORE") or 54.0)
        fuzzy_min_score = float(os.getenv("ODDS_API_IO_FUZZY_MATCH_MIN_SCORE") or 68.0)
        min_gap = float(os.getenv("ODDS_API_IO_MATCH_AMBIGUITY_MIN_GAP") or 7.0)
        if best_match is None or best_score < min_score:
            return None
        if best_quality == "fuzzy" and (best_score < fuzzy_min_score or (best_score - second_score) < min_gap):
            return None
        event["match_score"] = round(float(best_score), 3)
        event["match_quality"] = best_quality
        event["match_second_score"] = round(float(second_score), 3)
        event["match_score_gap"] = round(float(best_score - second_score), 3)
        return best_match

    cls._match_event = match_event_patched
    setattr(cls, PATCH_MARKER, True)
    return True


def _patch_team_aliases() -> bool:
    try:
        import app.utils as utils
    except Exception:
        return False
    aliases = getattr(utils, "TEAM_ALIAS_MAP", None)
    stops = getattr(utils, "TEAM_STOP_WORDS", None)
    if isinstance(aliases, dict):
        aliases.update({
            "arsenal fc": "arsenal", "arsenal": "arsenal", "atletico madrid": "atletico madrid",
            "atletico de madrid": "atletico madrid", "club atletico de madrid": "atletico madrid",
            "psg": "paris saint germain", "paris sg": "paris saint germain", "paris saint germain": "paris saint germain",
            "inter milan": "internazionale", "internazionale": "internazionale", "man utd": "manchester united",
            "man united": "manchester united", "manchester utd": "manchester united", "man city": "manchester city",
            "spurs": "tottenham", "tottenham hotspur": "tottenham", "bayern munich": "bayern munich",
            "fc bayern munich": "bayern munich", "bayer leverkusen": "bayer leverkusen",
            "athletic bilbao": "athletic club", "athletic club bilbao": "athletic club",
        })
    if isinstance(stops, set):
        stops.update({"f c", "football", "soccer", "team", "association"})
    return True


def _install_env_defaults() -> None:
    os.environ.setdefault("API_MATCHING_QUALITY_GUARD_ENABLED", "true")
    os.environ.setdefault("ODDS_API_IO_MATCH_MIN_SCORE", "54")
    os.environ.setdefault("ODDS_API_IO_FUZZY_MATCH_MIN_SCORE", "68")
    os.environ.setdefault("ODDS_API_IO_MATCH_AMBIGUITY_MIN_GAP", "7")
    os.environ.setdefault("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS", "3")
    os.environ.setdefault("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES", "true")
    os.environ.setdefault("ODDS_API_IO_REJECT_NON_FULL_TIME_MARKETS", "true")


def install() -> bool:
    _install_env_defaults()
    if not _truthy(os.getenv("API_MATCHING_QUALITY_GUARD_ENABLED"), True):
        return False
    changed = False
    changed = _patch_team_aliases() or changed
    changed = _patch_market_integrity() or changed
    changed = _patch_odds_api_io_provider() or changed
    return changed
