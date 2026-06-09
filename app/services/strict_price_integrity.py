from __future__ import annotations

"""Strict separation of price confirmations and context confirmations.

Context providers (sstats, clubelo, espn, football_data, weather, news, etc.)
can strengthen the model, but they must never count as confirmation that a
selected bookmaker price is real for the exact market/selection/point.
"""

import os
import re
from typing import Any

from app.services.publication_thresholds import publish_floor

ODDS_SOURCE_FIELDS = (
    "odds_sources",
    "odds_source_names",
    "price_sources",
    "price_source_names",
    "bookmaker_sources",
    "selected_odds_sources",
)

ODDS_SOURCE_COUNT_FIELDS = (
    "odds_sources_count",
    "price_sources_count",
    "independent_odds_sources_count",
    "exact_price_sources_count",
)

BOOKMAKER_FIELDS = (
    "bookmakers",
    "bookmaker_names",
    "books",
    "book_names",
    "selected_bookmakers",
    "exact_line_bookmakers",
)

BOOKMAKER_COUNT_FIELDS = (
    "books_count",
    "bookmakers_count",
    "bookmaker_count",
    "exact_line_bookmakers_count",
)

CONTEXT_SOURCE_FIELDS = (
    "confirmation_sources",
    "context_sources",
    "context_source_names",
    "merged_context_sources",
    "providers",
    "provider_names",
)

CONTEXT_ONLY_SOURCES = {
    "sstats",
    "clubelo",
    "espn",
    "football_data",
    "football_data_org",
    "thesportsdb",
    "openligadb",
    "openfootball",
    "weather",
    "weatherapi",
    "openweathermap",
    "meteostat",
    "newsapi",
    "currents",
    "gnews",
    "newsdata",
    "guardian",
    "self_history",
    "futrixmetrics",
}

PRICE_SOURCE_ALIASES = {
    "odds_api_io": "odds_api_io",
    "odds-api.io": "odds_api_io",
    "oddsapiio": "odds_api_io",
    "api_football": "api_football",
    "api-football": "api_football",
    "bzzoiro": "bzzoiro",
    "bzzoiro_event_odds": "bzzoiro",
    "oddspapi": "oddspapi",
    "odds_papi": "oddspapi",
    "sportlogic": "sportlogic",
    "allsportsapi": "allsportsapi",
    "bookies_api": "bookies_api",
    "sportsbook_api": "sportsbook_api",
    "oddsfeed": "oddsfeed",
}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        return [item for item in re.split(r"[,+;/|]+", value) if item.strip()]
    return []


def _containers(candidate: Any) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    summary = getattr(candidate, "source_summary", None)
    diagnostics = getattr(candidate, "diagnostics", None)
    integrity_report = getattr(candidate, "integrity_report", None)
    for value in (summary, diagnostics, integrity_report):
        if isinstance(value, dict):
            containers.append(value)
    return containers


def _offer_dicts(candidate: Any) -> list[dict[str, Any]]:
    raw = getattr(candidate, "raw_bucket_offers", None)
    if isinstance(raw, list) and raw:
        return [row for row in raw if isinstance(row, dict)]
    for container in _containers(candidate):
        for key in ("offers", "bucket_offers", "selected_offers", "raw_bucket_offers"):
            value = container.get(key)
            if isinstance(value, list) and value:
                return [row for row in value if isinstance(row, dict)]
    return []


def _canonical_price_source(value: Any) -> str | None:
    key = _norm(value)
    if not key:
        return None
    if key in CONTEXT_ONLY_SOURCES:
        return None
    if key in PRICE_SOURCE_ALIASES:
        return PRICE_SOURCE_ALIASES[key]
    for needle, canonical in PRICE_SOURCE_ALIASES.items():
        if needle in key:
            return canonical
    return key


def _line_side(candidate: Any) -> str:
    raw = " ".join(
        str(getattr(candidate, attr, "") or "")
        for attr in ("selection_key", "selection", "team_side")
    ).lower()
    if any(token in raw for token in ("over", "больше", "тб")):
        return "over"
    if any(token in raw for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in raw for token in ("yes", "да")):
        return "yes"
    if any(token in raw for token in ("no", "нет")):
        return "no"
    if "away" in raw:
        return "away"
    if "home" in raw:
        return "home"
    return _norm(raw)


def _same_line(candidate: Any, offer: dict[str, Any]) -> bool:
    family = _norm(getattr(candidate, "family", ""))
    offer_family = _norm(offer.get("family") or offer.get("market_key") or offer.get("market_name"))
    if family and offer_family and family != offer_family:
        return False
    cand_point = getattr(candidate, "point", None)
    offer_point = offer.get("point") if offer.get("point") is not None else offer.get("line") or offer.get("handicap")
    if cand_point not in (None, "") or offer_point not in (None, ""):
        if abs(_as_float(cand_point, -9999.0) - _as_float(offer_point, 9999.0)) > 1e-9:
            return False
    cand_side = _line_side(candidate)
    offer_text = " ".join(str(offer.get(key) or "") for key in ("selection", "selection_key", "team_side", "name", "label")).lower()
    if cand_side in {"over", "under", "yes", "no", "home", "away"}:
        if cand_side == "over" and not any(token in offer_text for token in ("over", "больше", "тб")):
            return False
        if cand_side == "under" and not any(token in offer_text for token in ("under", "меньше", "тм")):
            return False
        if cand_side == "yes" and not any(token in offer_text for token in ("yes", "да")):
            return False
        if cand_side == "no" and not any(token in offer_text for token in ("no", "нет")):
            return False
        if cand_side in {"home", "away"} and cand_side not in offer_text:
            # Team-name selections often do not expose side text, so do not fail hard here.
            pass
    return True


def _collect_price_sources(candidate: Any) -> set[str]:
    sources: set[str] = set()
    for offer in _offer_dicts(candidate):
        if not _same_line(candidate, offer):
            continue
        src = _canonical_price_source(offer.get("source"))
        if src:
            sources.add(src)
        metadata = offer.get("metadata") if isinstance(offer.get("metadata"), dict) else {}
        if metadata:
            src = _canonical_price_source(metadata.get("source") or metadata.get("provider"))
            if src:
                sources.add(src)
            # Accounts from the same API are useful diagnostics but not independent API sources.
            base = _canonical_price_source(offer.get("source"))
            account = str(metadata.get("odds_api_io_account") or "").strip().lower()
            if base and account and _truthy(os.getenv("STRICT_PRICE_COUNT_API_ACCOUNTS_AS_SOURCES"), False):
                sources.add(f"{base}:{account}")
    for container in _containers(candidate):
        for field in ODDS_SOURCE_FIELDS:
            for value in _values(container.get(field)):
                src = _canonical_price_source(value)
                if src:
                    sources.add(src)
        explicit = max(_as_int(container.get(field), 0) for field in ODDS_SOURCE_COUNT_FIELDS)
        selected_source = _canonical_price_source(container.get("selected_source") or container.get("source"))
        if explicit > len(sources):
            # Preserve explicit upstream odds-source count without letting generic sources_count leak in.
            for idx in range(len(sources), explicit):
                sources.add(f"explicit_price_source_{idx + 1}")
        if selected_source:
            sources.add(selected_source)
    return sources


def _collect_bookmakers(candidate: Any) -> set[str]:
    books: set[str] = set()
    for offer in _offer_dicts(candidate):
        if not _same_line(candidate, offer):
            continue
        book = _norm(offer.get("bookmaker") or offer.get("book") or offer.get("site"))
        if book:
            books.add(book)
    for container in _containers(candidate):
        for field in BOOKMAKER_FIELDS:
            for value in _values(container.get(field)):
                book = _norm(value)
                if book:
                    books.add(book)
        explicit = max(_as_int(container.get(field), 0) for field in BOOKMAKER_COUNT_FIELDS)
        if explicit > len(books):
            for idx in range(len(books), explicit):
                books.add(f"explicit_bookmaker_{idx + 1}")
        selected_book = _norm(container.get("selected_bookmaker") or container.get("bookmaker"))
        if selected_book:
            books.add(selected_book)
    selected_book = _norm(getattr(candidate, "bookmaker", None))
    if selected_book:
        books.add(selected_book)
    return books


def _collect_context_sources(candidate: Any) -> set[str]:
    sources: set[str] = set()
    for container in _containers(candidate):
        for field in CONTEXT_SOURCE_FIELDS:
            for value in _values(container.get(field)):
                key = _norm(value)
                if key:
                    sources.add(key)
    return sources


def _is_suspicious_low_total(candidate: Any, price_sources_count: int, bookmakers_count: int) -> str | None:
    family = _norm(getattr(candidate, "family", ""))
    if family != "totals":
        return None
    point = _as_float(getattr(candidate, "point", None), -1.0)
    odds = _as_float(getattr(candidate, "odds", 0.0), 0.0)
    side = _line_side(candidate)
    if side != "over":
        return None
    if abs(point - 1.5) < 1e-9:
        max_odds = _as_float(os.getenv("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS"), 1.65)
        min_books = max(3, _as_int(os.getenv("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS"), 3))
        if odds > max_odds and max(price_sources_count, bookmakers_count) < min_books:
            return f"suspicious_total_over_1_5_price:{odds:.2f}>{max_odds:.2f};confirmations={max(price_sources_count, bookmakers_count)}/{min_books}"
    return None


def annotate_candidate(candidate: Any) -> dict[str, Any]:
    price_sources = _collect_price_sources(candidate)
    bookmakers = _collect_bookmakers(candidate)
    context_sources = _collect_context_sources(candidate)
    price_sources_count = len(price_sources)
    bookmakers_count = len(bookmakers)
    context_sources_count = len(context_sources)
    summary = getattr(candidate, "source_summary", None)
    if not isinstance(summary, dict):
        summary = {}
        try:
            candidate.source_summary = summary
        except Exception:
            pass
    summary["price_sources"] = sorted(price_sources)
    summary["price_sources_count"] = price_sources_count
    summary["exact_price_sources_count"] = price_sources_count
    summary["exact_line_bookmakers"] = sorted(bookmakers)
    summary["exact_line_bookmakers_count"] = bookmakers_count
    summary["context_sources"] = sorted(context_sources)
    summary["context_sources_count"] = context_sources_count
    # Keep user-facing values honest for detailed reports/publisher.
    summary["odds_sources_count"] = price_sources_count
    report = {
        "price_sources_count": price_sources_count,
        "price_sources": sorted(price_sources),
        "exact_line_bookmakers_count": bookmakers_count,
        "exact_line_bookmakers": sorted(bookmakers),
        "context_sources_count": context_sources_count,
        "context_sources": sorted(context_sources),
    }
    try:
        candidate.integrity_report = {**dict(getattr(candidate, "integrity_report", {}) or {}), **report}
    except Exception:
        pass
    return report


def rejection_reasons(candidate: Any) -> list[str]:
    report = annotate_candidate(candidate)
    price_sources_count = int(report["price_sources_count"])
    bookmakers_count = int(report["exact_line_bookmakers_count"])
    reasons: list[str] = []
    min_price_sources = max(publish_floor(), _as_int(os.getenv("STRICT_PRICE_INTEGRITY_MIN_PRICE_SOURCES"), publish_floor()))
    if price_sources_count < min_price_sources:
        reasons.append(f"price_sources_below_min:{price_sources_count}/{min_price_sources}")
    min_books = max(publish_floor(), _as_int(os.getenv("STRICT_PRICE_INTEGRITY_MIN_BOOKMAKERS"), publish_floor()))
    if bookmakers_count < min_books:
        reasons.append(f"exact_bookmakers_below_min:{bookmakers_count}/{min_books}")
    suspicious = _is_suspicious_low_total(candidate, price_sources_count, bookmakers_count)
    if suspicious:
        reasons.append(suspicious)
    return reasons


def apply_strict_price_integrity(candidates: list[Any], rejections: dict[str, int] | None = None) -> tuple[list[Any], dict[str, Any]]:
    if not _truthy(os.getenv("STRICT_PRICE_INTEGRITY_ENABLED"), True):
        return list(candidates or []), {"enabled": False}
    out: list[Any] = []
    blocked: list[dict[str, Any]] = []
    rejection_counter: dict[str, int] = {}
    for candidate in list(candidates or []):
        reasons = rejection_reasons(candidate)
        if not reasons:
            out.append(candidate)
            continue
        try:
            candidate.integrity_status = "rejected"
            candidate.integrity_reasons = list(reasons)
        except Exception:
            pass
        for reason in reasons:
            key = "strict_price_integrity_" + re.sub(r"[^a-z0-9_]+", "_", reason.split(":", 1)[0].lower()).strip("_")
            rejection_counter[key] = rejection_counter.get(key, 0) + 1
            if isinstance(rejections, dict):
                rejections[key] = int(rejections.get(key, 0) or 0) + 1
        if len(blocked) < 12:
            blocked.append({
                "match_key": str(getattr(candidate, "match_key", "") or ""),
                "home_team": str(getattr(candidate, "home_team", "") or ""),
                "away_team": str(getattr(candidate, "away_team", "") or ""),
                "family": str(getattr(candidate, "family", "") or ""),
                "selection": str(getattr(candidate, "selection", "") or ""),
                "point": getattr(candidate, "point", None),
                "odds": getattr(candidate, "odds", None),
                "reasons": reasons,
                "report": dict(getattr(candidate, "integrity_report", {}) or {}),
            })
    return out, {
        "enabled": True,
        "before": len(list(candidates or [])),
        "after": len(out),
        "blocked": len(list(candidates or [])) - len(out),
        "rejections": dict(sorted(rejection_counter.items())),
        "blocked_sample": blocked,
    }
