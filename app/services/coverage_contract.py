from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


CONTEXT_ONLY_SOURCES = {
    "api_football",
    "clubelo",
    "currents",
    "espn",
    "football_data",
    "futrixmetrics",
    "gnews",
    "news",
    "newsapi",
    "openfootball",
    "openligadb",
    "open_meteo",
    "openweathermap",
    "self_history",
    "thesportsdb",
    "weather",
    "weatherapi",
}

AGGREGATE_CONTEXT_SOURCES = {"ensemble", "market_signal", "unknown"}

ODDS_SOURCE_ALIASES = {
    "account1": "odds_api_io",
    "account2": "odds_api_io",
    "bzzoiro_predictions": "bzzoiro",
    "bzzoiro_v2": "bzzoiro",
    "oddsapiio": "odds_api_io",
    "odds_api": "odds_api_io",
    "rapidapi_odds": "rapidapi_odds_bridge",
    "rapidapi_odds_feed": "rapidapi_odds_bridge",
    "sportlogic_controlled": "sportlogic",
    "sstats_current_odds": "sstats",
}


@dataclass(frozen=True)
class CoverageContract:
    min_odds_sources: int = 2
    min_context_sources: int = 2
    min_books: int = 2
    context_sources_do_not_confirm_price: bool = True
    count_api_accounts_as_sources: bool = False


@dataclass(frozen=True)
class CoverageDecision:
    passed: bool
    reasons: tuple[str, ...]
    report: dict[str, Any]


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def contract_from_settings(settings: Any | None = None) -> CoverageContract:
    def setting(name: str, default: Any) -> Any:
        if settings is not None and getattr(settings, name, None) is not None:
            return getattr(settings, name)
        return default

    min_odds = max(
        2,
        _as_int(
            os.getenv("PUBLISH_MIN_ODDS_SOURCES")
            or os.getenv("TELEGRAM_MIN_ODDS_SOURCES")
            or os.getenv("MIN_SOURCES_PUBLISH")
            or setting("min_sources_publish", 2),
            2,
        ),
    )
    min_context = max(
        2,
        _as_int(
            os.getenv("PUBLISH_MIN_CONTEXT_SOURCES")
            or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH")
            or setting("min_context_sources_publish", 2),
            2,
        ),
    )
    min_books = max(
        2,
        _as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH") or setting("min_books_publish", 2), 2),
    )
    return CoverageContract(
        min_odds_sources=min_odds,
        min_context_sources=min_context,
        min_books=min_books,
        context_sources_do_not_confirm_price=_truthy(os.getenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE"), True),
        count_api_accounts_as_sources=_truthy(os.getenv("STRICT_PRICE_COUNT_API_ACCOUNTS_AS_SOURCES"), False),
    )


def normalize_source(value: Any, *, count_api_accounts_as_sources: bool = False) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return ""
    raw = "_".join(part for part in raw.split("_") if part)
    if not count_api_accounts_as_sources and raw.startswith("odds_api_io_account"):
        return "odds_api_io"
    if not count_api_accounts_as_sources and raw in {"account1", "account2"}:
        return "odds_api_io"
    return ODDS_SOURCE_ALIASES.get(raw, raw)


def _get(container: Any, field: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(field, default)
    return getattr(container, field, default)


def _iter_offer_rows(candidate: Any) -> list[Any]:
    rows = _get(candidate, "raw_bucket_offers", None)
    if isinstance(rows, list) and rows:
        return rows
    source_summary = _get(candidate, "source_summary", {}) or {}
    if isinstance(source_summary, dict):
        for key in ("offers", "bucket_offers", "selected_offers", "raw_bucket_offers"):
            value = source_summary.get(key)
            if isinstance(value, list) and value:
                return value
    return []


def _sources_from_summary(candidate: Any) -> set[str]:
    source_summary = _get(candidate, "source_summary", {}) or {}
    if not isinstance(source_summary, dict):
        return set()
    found: set[str] = set()
    for key in ("sources", "odds_sources", "price_sources", "selected_odds_sources", "selected_price_sources"):
        value = source_summary.get(key)
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, (list, tuple, set)):
            found.update(str(item) for item in value)
    return found


def odds_sources_for_candidate(candidate: Any, contract: CoverageContract | None = None) -> set[str]:
    contract = contract or CoverageContract()
    sources: set[str] = set()
    rows = _iter_offer_rows(candidate)
    for row in rows:
        source = normalize_source(_get(row, "source"), count_api_accounts_as_sources=contract.count_api_accounts_as_sources)
        if not source:
            continue
        if contract.context_sources_do_not_confirm_price and source in CONTEXT_ONLY_SOURCES:
            continue
        sources.add(source)
    if sources:
        return sources
    for source in _sources_from_summary(candidate):
        normalized = normalize_source(source, count_api_accounts_as_sources=contract.count_api_accounts_as_sources)
        if normalized and not (contract.context_sources_do_not_confirm_price and normalized in CONTEXT_ONLY_SOURCES):
            sources.add(normalized)
    return sources


def context_sources_for_candidate(candidate: Any) -> set[str]:
    source_summary = _get(candidate, "source_summary", {}) or {}
    sources: set[str] = set()
    if isinstance(source_summary, dict):
        for key in ("context_sources", "confirmation_sources", "merged_context_sources"):
            value = source_summary.get(key)
            if isinstance(value, str):
                sources.add(value)
            elif isinstance(value, (list, tuple, set)):
                sources.update(str(item) for item in value)
        context_source = source_summary.get("context_source")
        if context_source:
            sources.add(str(context_source))
    normalized = {normalize_source(item) for item in sources}
    return {item for item in normalized if item and item not in AGGREGATE_CONTEXT_SOURCES}


def books_for_candidate(candidate: Any) -> set[str]:
    books: set[str] = set()
    for row in _iter_offer_rows(candidate):
        book = str(_get(row, "bookmaker") or "").strip().lower()
        if book:
            books.add(book)
    if books:
        return books
    source_summary = _get(candidate, "source_summary", {}) or {}
    if isinstance(source_summary, dict):
        value = source_summary.get("books")
        if isinstance(value, str):
            books.add(value.strip().lower())
        elif isinstance(value, (list, tuple, set)):
            books.update(str(item).strip().lower() for item in value if str(item).strip())
    return books


def evaluate_publish_candidate(candidate: Any, settings: Any | None = None) -> CoverageDecision:
    contract = contract_from_settings(settings)
    raw_offer_rows = _iter_offer_rows(candidate)
    odds_sources = odds_sources_for_candidate(candidate, contract)
    context_sources = context_sources_for_candidate(candidate)
    books = books_for_candidate(candidate)
    books_count = max(len(books), _as_int(_get(candidate, "books_count", 0), 0))
    odds_source_count = len(odds_sources)
    if not raw_offer_rows and not odds_sources:
        odds_source_count = _as_int(_get(candidate, "sources_count", 0), 0)
    context_source_count = len(context_sources)

    reasons: list[str] = []
    if odds_source_count < contract.min_odds_sources:
        reasons.append(f"insufficient_odds_sources:{odds_source_count}/{contract.min_odds_sources}")
    if context_source_count < contract.min_context_sources:
        reasons.append(f"insufficient_context_sources:{context_source_count}/{contract.min_context_sources}")
    if books_count < contract.min_books:
        reasons.append(f"insufficient_books:{books_count}/{contract.min_books}")

    report = {
        "min_odds_sources": contract.min_odds_sources,
        "min_context_sources": contract.min_context_sources,
        "min_books": contract.min_books,
        "odds_sources": sorted(odds_sources),
        "odds_sources_count": odds_source_count,
        "context_sources": sorted(context_sources),
        "context_sources_count": context_source_count,
        "books": sorted(books),
        "books_count": books_count,
    }
    return CoverageDecision(passed=not reasons, reasons=tuple(reasons), report=report)
