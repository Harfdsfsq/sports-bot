from __future__ import annotations

import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.services.publication_thresholds import (
    publish_min_books,
    publish_min_context_sources,
    publish_min_odds_sources,
)


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

AGGREGATE_CONTEXT_SOURCES = {"ensemble", "market", "market_signal", "unknown"}
CONTEXT_SOURCE_INDEX_PATH = Path(".data/exports/latest-context-source-index.json")
_CONTEXT_SOURCE_INDEX_BUILD_ATTEMPTED = False

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

ODDS_SOURCE_LIST_KEYS = {
    "sources",
    "odds_sources",
    "offer_sources",
    "price_sources",
    "selected_odds_sources",
    "selected_price_sources",
    "exact_odds_sources",
    "independent_sources",
    "publication_odds_sources",
    "provider_sources",
}

ODDS_SOURCE_COUNT_KEYS = {
    "sources_count",
    "odds_sources_count",
    "odds_source_count",
    "independent_odds_sources",
    "independent_odds_sources_count",
    "independent_odds_source_count",
    "publication_odds_sources_count",
    "exact_odds_sources_count",
    "price_sources_count",
}

CONTEXT_SOURCE_LIST_KEYS = {
    "context_sources",
    "confirmation_sources",
    "merged_context_sources",
    "context_provider_sources",
}

CONTEXT_SOURCE_COUNT_KEYS = {
    "context_sources_count",
    "context_source_count",
    "confirmation_sources_count",
    "merged_context_sources_count",
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
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
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

    min_odds = publish_min_odds_sources(settings)
    min_context = publish_min_context_sources(settings)
    min_books = publish_min_books(settings)
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


def _set(container: Any, field: str, value: Any) -> None:
    if isinstance(container, dict):
        container[field] = value
        return
    try:
        setattr(container, field, value)
    except Exception:
        return


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


def _iter_dicts(value: Any, *, max_depth: int = 4) -> Iterable[dict[str, Any]]:
    if max_depth < 0:
        return
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            if isinstance(nested, dict):
                yield from _iter_dicts(nested, max_depth=max_depth - 1)
            elif isinstance(nested, list):
                for item in nested[:20]:
                    if isinstance(item, dict):
                        yield from _iter_dicts(item, max_depth=max_depth - 1)


def _candidate_dict_views(candidate: Any) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    if isinstance(candidate, dict):
        views.extend(_iter_dicts(candidate, max_depth=3))
    for attr in ("source_summary", "diagnostics", "integrity_report", "analysis"):
        value = _get(candidate, attr, None)
        if isinstance(value, dict):
            views.extend(_iter_dicts(value, max_depth=3))
    unique: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for view in views:
        ident = id(view)
        if ident not in seen_ids:
            unique.append(view)
            seen_ids.add(ident)
    return unique


def _split_sources(value: Any) -> set[str]:
    found: set[str] = set()
    if value in (None, ""):
        return found
    if isinstance(value, str):
        found.update(part.strip() for part in re.split(r"[,;+|/]", value) if part.strip())
        return found
    if isinstance(value, (list, tuple, set)):
        for item in value:
            found.update(_split_sources(item))
        return found
    if isinstance(value, dict):
        for key, item_value in value.items():
            if isinstance(item_value, (bool, int, float, str)) and str(key or "").strip():
                found.add(str(key).strip())
            else:
                found.update(_split_sources(item_value))
    return found


def _sources_from_summary(candidate: Any) -> set[str]:
    found: set[str] = set()
    for view in _candidate_dict_views(candidate):
        for key in ODDS_SOURCE_LIST_KEYS:
            if key in view:
                found.update(_split_sources(view.get(key)))
    return found


def _declared_count(candidate: Any, keys: set[str]) -> tuple[int, list[str]]:
    count = 0
    basis: list[str] = []
    for view in _candidate_dict_views(candidate):
        for key in keys:
            if key not in view:
                continue
            value = _as_int(view.get(key), -1)
            if value >= 0:
                if value > count:
                    count = value
                basis.append(key)
    for key in keys:
        value = _as_int(_get(candidate, key, None), -1)
        if value >= 0:
            if value > count:
                count = value
            basis.append(f"candidate.{key}")
    return count, sorted(set(basis))


def _context_key_part(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _date_key(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _candidate_context_index_keys(candidate: Any) -> list[str]:
    raw_key = str(_get(candidate, "match_key", "") or "")
    keys: list[str] = []
    if raw_key:
        keys.append(raw_key)
        keys.append(raw_key.replace("_", " "))
    home = _context_key_part(_get(candidate, "home_team", ""))
    away = _context_key_part(_get(candidate, "away_team", ""))
    day = _date_key(_get(candidate, "commence_time", "")) or _date_key(raw_key)
    if home and away and day:
        keys.append(f"soccer|{home}|{away}|{day}")
        keys.append(f"soccer|{away}|{home}|{day}")
    return list(dict.fromkeys(key for key in keys if key))


def _ensure_context_source_index() -> None:
    global _CONTEXT_SOURCE_INDEX_BUILD_ATTEMPTED
    if _CONTEXT_SOURCE_INDEX_BUILD_ATTEMPTED:
        return
    _CONTEXT_SOURCE_INDEX_BUILD_ATTEMPTED = True
    if CONTEXT_SOURCE_INDEX_PATH.exists() and CONTEXT_SOURCE_INDEX_PATH.stat().st_size > 0:
        return
    if not _truthy(os.getenv("PUBLISH_COVERAGE_CONTEXT_INDEX_BUILD_ON_DEMAND"), True):
        return
    try:
        builder_path = Path("scripts/build_context_source_index.py")
        if not builder_path.exists():
            builder_path = Path(__file__).resolve().parents[2] / "scripts" / "build_context_source_index.py"
        if not builder_path.exists():
            return
        spec = importlib.util.spec_from_file_location("harizon_context_source_index_builder", builder_path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        main = getattr(module, "main", None)
        if callable(main):
            main()
    except Exception:
        return


def _context_index_sources(candidate: Any) -> set[str]:
    if not _truthy(os.getenv("PUBLISH_COVERAGE_CONTEXT_INDEX_BRIDGE_ENABLED"), True):
        return set()
    try:
        if not CONTEXT_SOURCE_INDEX_PATH.exists() or CONTEXT_SOURCE_INDEX_PATH.stat().st_size <= 0:
            _ensure_context_source_index()
        if not CONTEXT_SOURCE_INDEX_PATH.exists() or CONTEXT_SOURCE_INDEX_PATH.stat().st_size <= 0:
            return set()
        payload = json.loads(CONTEXT_SOURCE_INDEX_PATH.read_text(encoding="utf-8"))
        by_match = payload.get("by_match") if isinstance(payload, dict) else {}
        if not isinstance(by_match, dict):
            return set()
    except Exception:
        return set()
    found: set[str] = set()
    keys = _candidate_context_index_keys(candidate)
    for key in keys:
        value = by_match.get(key)
        if isinstance(value, list):
            found.update(str(item) for item in value if str(item).strip())
    if found:
        try:
            summary = dict(_get(candidate, "source_summary", {}) or {})
            summary["context_index_bridge_keys"] = keys
            summary["context_index_bridge_sources"] = sorted(normalize_source(item) for item in found if normalize_source(item))
            _set(candidate, "source_summary", summary)
        except Exception:
            pass
    return found


def odds_sources_for_candidate(candidate: Any, contract: CoverageContract | None = None) -> set[str]:
    contract = contract or CoverageContract()
    sources: set[str] = set()
    offer_rows = _iter_offer_rows(candidate)
    for row in offer_rows:
        source = normalize_source(_get(row, "source"), count_api_accounts_as_sources=contract.count_api_accounts_as_sources)
        if not source:
            continue
        if contract.context_sources_do_not_confirm_price and source in CONTEXT_ONLY_SOURCES:
            continue
        sources.add(source)
    if offer_rows:
        return sources
    for source in _sources_from_summary(candidate):
        normalized = normalize_source(source, count_api_accounts_as_sources=contract.count_api_accounts_as_sources)
        if normalized and not (contract.context_sources_do_not_confirm_price and normalized in CONTEXT_ONLY_SOURCES):
            sources.add(normalized)
    return sources


def context_sources_for_candidate(candidate: Any) -> set[str]:
    sources: set[str] = set()
    for view in _candidate_dict_views(candidate):
        for key in CONTEXT_SOURCE_LIST_KEYS:
            if key in view:
                sources.update(_split_sources(view.get(key)))
        context_source = view.get("context_source")
        if context_source:
            sources.add(str(context_source))
    sources.update(_context_index_sources(candidate))
    normalized = {normalize_source(item) for item in sources}
    return {item for item in normalized if item and item not in AGGREGATE_CONTEXT_SOURCES}


def books_for_candidate(candidate: Any) -> set[str]:
    books: set[str] = set()
    for row in _iter_offer_rows(candidate):
        book = str(
            _get(row, "bookmaker")
            or _get(row, "bookmaker_slug")
            or _get(row, "book")
            or _get(row, "sportsbook")
            or ""
        ).strip().lower()
        if book:
            books.add(book)
    for view in _candidate_dict_views(candidate):
        for key in (
            "books",
            "bookmakers",
            "selected_books",
            "bookmaker",
            "bookmaker_slug",
            "selected_bookmaker",
            "selected_bookmaker_slug",
            "sportsbook",
            "book",
        ):
            if key not in view:
                continue
            value = view.get(key)
            if isinstance(value, str):
                books.update(part.strip().lower() for part in re.split(r"[,;+|/]", value) if part.strip())
            elif isinstance(value, (list, tuple, set)):
                books.update(str(item).strip().lower() for item in value if str(item).strip())
    return books


def line_sources_for_candidate(candidate: Any) -> set[str]:
    sources: set[str] = set()
    for row in _iter_offer_rows(candidate):
        book = str(
            _get(row, "bookmaker")
            or _get(row, "bookmaker_slug")
            or _get(row, "book")
            or _get(row, "sportsbook")
            or ""
        ).strip().lower()
        if book:
            sources.add(f"book:{book}")
            continue
        source = normalize_source(_get(row, "source"))
        if source:
            sources.add(f"source:{source}")
    for view in _candidate_dict_views(candidate):
        for key in ("line_sources", "price_confirmations", "price_sources"):
            if key in view:
                sources.update(_split_sources(view.get(key)))
    return {str(item).strip().lower() for item in sources if str(item).strip()}


def publication_odds_source_report(candidate: Any, contract: CoverageContract | None = None) -> dict[str, Any]:
    contract = contract or CoverageContract()
    odds_sources = odds_sources_for_candidate(candidate, contract)
    declared_count, declared_basis = _declared_count(candidate, ODDS_SOURCE_COUNT_KEYS)
    has_named_evidence = bool(_iter_offer_rows(candidate) or _sources_from_summary(candidate))
    source_count = len(odds_sources) if has_named_evidence else declared_count
    basis: list[str] = []
    if _iter_offer_rows(candidate):
        basis.append("raw_bucket_offers.sources")
    if odds_sources:
        basis.append("normalized_odds_source_lists")
    if not has_named_evidence:
        basis.extend(declared_basis)
    return {
        "odds_sources": sorted(odds_sources),
        "odds_sources_count": source_count,
        "declared_odds_sources_count": declared_count,
        "named_odds_sources_count": len(odds_sources),
        "basis": sorted(set(basis)) or ["none"],
    }


def evaluate_publish_candidate(candidate: Any, settings: Any | None = None) -> CoverageDecision:
    contract = contract_from_settings(settings)
    odds_report = publication_odds_source_report(candidate, contract)
    context_sources = context_sources_for_candidate(candidate)
    books = books_for_candidate(candidate)
    line_sources = line_sources_for_candidate(candidate)
    books_count = max(len(books), _as_int(_get(candidate, "books_count", 0), 0))
    line_sources_count = max(len(line_sources), books_count)
    odds_sources = set(odds_report["odds_sources"])
    odds_source_count = int(odds_report["odds_sources_count"])
    context_declared_count, context_basis = _declared_count(candidate, CONTEXT_SOURCE_COUNT_KEYS)
    context_source_count = len(context_sources) if context_sources else context_declared_count

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
        "declared_odds_sources_count": odds_report["declared_odds_sources_count"],
        "named_odds_sources_count": odds_report["named_odds_sources_count"],
        "odds_sources_basis": odds_report["basis"],
        "context_sources": sorted(context_sources),
        "context_sources_count": context_source_count,
        "declared_context_sources_count": context_declared_count,
        "context_sources_basis": context_basis,
        "books": sorted(books),
        "books_count": books_count,
        "line_sources": sorted(line_sources),
        "line_sources_count": line_sources_count,
        "price_sources_count": line_sources_count,
    }
    return CoverageDecision(passed=not reasons, reasons=tuple(reasons), report=report)


def sync_candidate_publish_coverage(candidate: Any, settings: Any | None = None) -> CoverageDecision:
    """Normalize the final publish-coverage fields before any Telegram/fallback guard reads them.

    Older pipeline stages may leave stale values such as source_summary.odds_sources_count=1 while
    raw offers or a nested publish_coverage_contract already prove 2+ sources. This function makes
    the richest coverage report the single value of truth for the rest of the run.
    """
    decision = evaluate_publish_candidate(candidate, settings)
    report = dict(decision.report)

    source_summary = dict(_get(candidate, "source_summary", {}) or {})
    source_summary["publish_coverage_contract"] = report
    source_summary["publish_coverage_passed"] = bool(decision.passed)
    source_summary["publish_coverage_reasons"] = list(decision.reasons)
    source_summary["odds_sources_count"] = int(report.get("odds_sources_count") or 0)
    source_summary["independent_odds_sources_count"] = int(report.get("odds_sources_count") or 0)
    source_summary["price_sources_count"] = int(report.get("odds_sources_count") or 0)
    source_summary["exact_odds_sources"] = list(report.get("odds_sources") or [])
    source_summary["odds_sources"] = list(report.get("odds_sources") or [])
    source_summary["context_sources_count"] = int(report.get("context_sources_count") or 0)
    source_summary["context_sources"] = list(report.get("context_sources") or [])
    source_summary["books_count"] = int(report.get("books_count") or 0)
    source_summary["books"] = list(report.get("books") or [])
    source_summary["line_sources_count"] = int(report.get("line_sources_count") or 0)
    source_summary["price_sources_count"] = int(report.get("price_sources_count") or 0)
    source_summary["line_sources"] = list(report.get("line_sources") or [])
    _set(candidate, "source_summary", source_summary)

    diagnostics = dict(_get(candidate, "diagnostics", {}) or {})
    diagnostics["publish_coverage_contract"] = report
    _set(candidate, "diagnostics", diagnostics)

    _set(candidate, "sources_count", max(_as_int(_get(candidate, "sources_count", 0), 0), int(report.get("odds_sources_count") or 0)))
    _set(candidate, "books_count", max(_as_int(_get(candidate, "books_count", 0), 0), int(report.get("books_count") or 0)))
    return decision
