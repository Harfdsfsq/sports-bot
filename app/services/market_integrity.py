from __future__ import annotations

from dataclasses import dataclass, asdict
import os
import re
from statistics import median
from typing import Any


@dataclass(slots=True)
class IntegrityDecision:
    passed: bool
    reasons: list[str]
    report: dict[str, Any]

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return default


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _offer_dicts(candidate: Any) -> list[dict[str, Any]]:
    raw = getattr(candidate, "raw_bucket_offers", None)
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, dict)]
    summary = getattr(candidate, "source_summary", None) or {}
    for key in ("offers", "bucket_offers", "selected_offers", "raw_bucket_offers"):
        value = summary.get(key) if isinstance(summary, dict) else None
        if isinstance(value, list) and value:
            return [x for x in value if isinstance(x, dict)]
    diagnostics = getattr(candidate, "diagnostics", None) or {}
    value = diagnostics.get("offers") if isinstance(diagnostics, dict) else None
    if isinstance(value, list) and value:
        return [x for x in value if isinstance(x, dict)]
    return []


def _market_text(candidate: Any) -> str:
    parts: list[str] = []
    for attr in ("family", "selection", "selection_key", "bookmaker", "model_mode"):
        parts.append(str(getattr(candidate, attr, "") or ""))
    summary = getattr(candidate, "source_summary", None) or {}
    diagnostics = getattr(candidate, "diagnostics", None) or {}
    if isinstance(summary, dict):
        for key in ("selected_market", "market_name", "market_key", "market_subtype", "selected_source"):
            parts.append(str(summary.get(key) or ""))
    if isinstance(diagnostics, dict):
        for key in ("market_name", "market_key", "market_subtype"):
            parts.append(str(diagnostics.get(key) or ""))
    for offer in _offer_dicts(candidate)[:12]:
        for key in ("family", "market_name", "market_key", "market_subtype", "selection", "label", "name"):
            parts.append(str(offer.get(key) or ""))
    return " ".join(parts).lower()


def _source_is_price_source(source: str) -> bool:
    source_low = _norm(source)
    if not source_low:
        return False
    context_tokens = (
        "news", "gnews", "newsapi", "currents", "guardian", "newsdata",
        "weather", "openmeteo", "open_meteo", "openweathermap", "weatherapi", "meteostat",
        "clubelo", "wikidata", "futrix", "sstats_context", "bzzoiro_context",
        "thesportsdb_context", "football_data_context", "api_football_context",
    )
    return not any(token in source_low for token in context_tokens)


def _offer_source(offer: dict[str, Any]) -> str:
    for key in ("source", "provider", "api", "origin", "source_name"):
        value = str(offer.get(key) or "").strip()
        if value:
            return value
    return ""


def _offer_bookmaker(offer: dict[str, Any]) -> str:
    for key in ("bookmaker", "book", "bookie", "sportsbook", "site"):
        value = str(offer.get(key) or "").strip()
        if value:
            return value
    return ""


def _sources_count(candidate: Any) -> int:
    value = _int(getattr(candidate, "sources_count", 0), 0)
    offers = _offer_dicts(candidate)
    if offers:
        price_sources = {
            _norm(_offer_source(x))
            for x in offers
            if _source_is_price_source(_offer_source(x))
        }
        value = max(value if not _truthy(os.getenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE"), True) else 0, len(price_sources))
    return value


def _books_count(candidate: Any) -> int:
    value = _int(getattr(candidate, "books_count", 0), 0)
    offers = _offer_dicts(candidate)
    if offers:
        value = max(value, len({_norm(_offer_bookmaker(x)) for x in offers if _offer_bookmaker(x)}))
    return value


def _price_from_offer(offer: dict[str, Any]) -> float | None:
    for key in ("price", "odds", "decimal_odds", "value"):
        value = _float(offer.get(key))
        if value and value > 1.0:
            return value
    return None


def _best_price(candidate: Any) -> float:
    prices: list[float] = []
    for attr in ("odds", "selected_odds", "price_used_for_ev"):
        value = _float(getattr(candidate, attr, None))
        if value and value > 1.0:
            prices.append(value)
    for offer in _offer_dicts(candidate):
        value = _price_from_offer(offer)
        if value and value > 1.0:
            prices.append(value)
    return max(prices) if prices else 0.0


def _offer_family(offer: dict[str, Any]) -> str:
    text = " ".join(str(offer.get(k) or "") for k in ("family", "market_name", "market_key", "market_subtype", "market", "name"))
    text_low = text.lower()
    if any(token in text_low for token in ("team total", "individual total", "итб", "итм")):
        return "teamtotals"
    if any(token in text_low for token in ("total", "over/under", "goals over", "goals under", "тотал", "больше", "меньше")):
        return "totals"
    if any(token in text_low for token in ("spread", "handicap", "фора")):
        return "spreads"
    if any(token in text_low for token in ("moneyline", "1x2", "winner", "match winner", "побед")):
        return "h2h"
    return _norm(offer.get("family"))


def _offer_point(offer: dict[str, Any]) -> float | None:
    for key in ("point", "line", "handicap", "total", "points"):
        value = _float(offer.get(key))
        if value is not None:
            return value
    text = " ".join(str(offer.get(k) or "") for k in ("selection", "label", "name", "market_name", "market_key"))
    match = re.search(r"(?<!\d)([0-9]+(?:[\.,][02575])?)(?!\d)", text)
    return _float(match.group(1)) if match else None


def _offer_selection(offer: dict[str, Any]) -> str:
    text = " ".join(str(offer.get(k) or "") for k in ("selection", "label", "name", "outcome", "side"))
    low = text.lower()
    if re.search(r"\b(over|o|больше|бол)\b", low):
        return "over"
    if re.search(r"\b(under|u|меньше|мен)\b", low):
        return "under"
    return _norm(text)


def _candidate_selection(candidate: Any) -> str:
    text = " ".join(str(getattr(candidate, attr, "") or "") for attr in ("selection", "selection_key", "market", "label"))
    low = text.lower()
    if re.search(r"\b(over|o|больше|бол)\b", low):
        return "over"
    if re.search(r"\b(under|u|меньше|мен)\b", low):
        return "under"
    return _norm(text)


def _exact_line_offers(candidate: Any, family: str, point: float | None, selection: str) -> list[dict[str, Any]]:
    if point is None:
        return []
    out: list[dict[str, Any]] = []
    for offer in _offer_dicts(candidate):
        price = _price_from_offer(offer)
        if not price:
            continue
        if _offer_family(offer) != family:
            continue
        offer_point = _offer_point(offer)
        if offer_point is None or abs(float(offer_point) - float(point)) > 0.001:
            continue
        offer_selection = _offer_selection(offer)
        if selection in {"over", "under"} and offer_selection != selection:
            continue
        out.append(offer)
    return out


def _price_dispersion_from_prices(prices: list[float]) -> float | None:
    if len(prices) < 2:
        return None
    med = median(prices)
    if med <= 0:
        return None
    return max(abs(p - med) / med for p in prices) * 100.0


def _price_dispersion(candidate: Any) -> float | None:
    prices = []
    for offer in _offer_dicts(candidate):
        value = _price_from_offer(offer)
        if value and value > 1.0:
            prices.append(value)
    return _price_dispersion_from_prices(prices)


def validate_candidate(candidate: Any) -> IntegrityDecision:
    reasons: list[str] = []
    family = str(getattr(candidate, "family", "") or "").strip()
    family_low = family.lower()
    point = _float(getattr(candidate, "point", None))
    price = _best_price(candidate)
    books = _books_count(candidate)
    sources = _sources_count(candidate)
    text = _market_text(candidate)
    selection = _candidate_selection(candidate)
    exact_offers = _exact_line_offers(candidate, family_low, point, selection)
    exact_books = len({_norm(_offer_bookmaker(x)) for x in exact_offers if _offer_bookmaker(x)})
    exact_sources = len({_norm(_offer_source(x)) for x in exact_offers if _source_is_price_source(_offer_source(x))})
    exact_prices = [p for p in (_price_from_offer(x) for x in exact_offers) if p]
    exact_median = median(exact_prices) if exact_prices else None
    dispersion = _price_dispersion(candidate)
    exact_dispersion = _price_dispersion_from_prices(exact_prices)

    min_books = _int(os.getenv("MARKET_INTEGRITY_MIN_BOOKS"), 2)
    min_sources = _int(os.getenv("MARKET_INTEGRITY_MIN_SOURCES"), 1)
    strict_single_source_books = _int(os.getenv("MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS"), 3)
    exact_price_required = _truthy(os.getenv("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES"), True)

    if books < min_books and sources < 2:
        reasons.append(f"insufficient_market_depth:books={books},sources={sources}")

    if sources < min_sources:
        reasons.append(f"insufficient_sources:{sources}/{min_sources}")

    if sources < 2 and books < strict_single_source_books and family_low in {"totals", "h2h", "spreads"}:
        model_mode = str(getattr(candidate, "model_mode", "") or "").lower()
        if "controlled" in model_mode or "market" in model_mode or "fallback" in model_mode:
            reasons.append(f"single_source_market_guard:books={books},sources={sources}")

    if family_low == "totals":
        if point is None:
            reasons.append("totals_missing_point")
        if re.search(r"\b(corner|corners|углов|угловые)\b", text):
            reasons.append("totals_family_contains_corners")
        if re.search(r"\b(ht|1st half|first half|half time|первый тайм|тайм)\b", text):
            reasons.append("totals_family_contains_half_time")

        max_over15 = _float(os.getenv("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS"), 1.65) or 1.65
        max_over20 = _float(os.getenv("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS"), 2.05) or 2.05
        min_exact_books = _int(os.getenv("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS"), 3)
        max_exact_delta = _float(os.getenv("MARKET_INTEGRITY_MAX_EXACT_LINE_DELTA_PCT"), 18.0) or 18.0

        if point is not None and selection == "over" and point <= 1.5 and price > max_over15:
            if exact_price_required and exact_books < min_exact_books:
                reasons.append(
                    f"suspicious_low_total_exact_depth:point={point:g},odds={price:.2f},exact_books={exact_books},exact_sources={exact_sources}"
                )
            else:
                reasons.append(f"suspicious_low_total_price:point={point:g},odds={price:.2f},books={books},sources={sources}")
        if point is not None and point <= 2.0 and price > max_over20 and not (sources >= 2 and books >= 2):
            reasons.append(f"suspicious_total_2_price:point={point:g},odds={price:.2f},books={books},sources={sources}")
        if exact_median and price > exact_median * (1.0 + max_exact_delta / 100.0):
            reasons.append(
                f"selected_price_above_exact_market_median:odds={price:.2f},median={exact_median:.2f},delta_limit={max_exact_delta:.1f}%"
            )

    if family_low == "teamtotals" and not _truthy(os.getenv("TEAM_TOTALS_PUBLICATION_ENABLED"), False):
        reasons.append("team_totals_quarantined")

    if family_low == "spreads" and _truthy(os.getenv("DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED"), True):
        reasons.append("spreads_quarantined")

    if dispersion is not None:
        max_disp = _float(os.getenv("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT"), 30.0) or 30.0
        if dispersion > max_disp and sources < 2:
            reasons.append(f"single_source_outlier_dispersion:{dispersion:.1f}%")
    if exact_dispersion is not None:
        max_exact_disp = _float(os.getenv("MARKET_INTEGRITY_MAX_EXACT_PRICE_DISPERSION_PCT"), 22.0) or 22.0
        if exact_dispersion > max_exact_disp:
            reasons.append(f"exact_line_outlier_dispersion:{exact_dispersion:.1f}%")

    report = {
        "family": family,
        "point": point,
        "selection": selection,
        "price": price,
        "books_count": books,
        "sources_count": sources,
        "exact_books_count": exact_books,
        "exact_sources_count": exact_sources,
        "exact_price_median": round(float(exact_median), 3) if exact_median is not None else None,
        "price_dispersion_pct": round(dispersion, 3) if dispersion is not None else None,
        "exact_price_dispersion_pct": round(exact_dispersion, 3) if exact_dispersion is not None else None,
        "market_text_sample": text[:220],
    }
    return IntegrityDecision(passed=not reasons, reasons=reasons, report=report)


def filter_candidates(candidates: list[Any], rejections: dict[str, int] | None = None) -> list[Any]:
    out: list[Any] = []
    rej = rejections if isinstance(rejections, dict) else {}
    for candidate in candidates:
        decision = validate_candidate(candidate)
        try:
            candidate.integrity_status = "passed" if decision.passed else "rejected"
            candidate.integrity_reasons = list(decision.reasons)
            candidate.integrity_report = dict(decision.report)
        except Exception:
            pass
        if decision.passed:
            out.append(candidate)
            continue
        for reason in decision.reasons:
            key = "market_integrity_" + re.sub(r"[^a-z0-9_]+", "_", reason.split(":", 1)[0].lower()).strip("_")
            rej[key] = int(rej.get(key, 0) or 0) + 1
    return out


def install_candidate_factory_patch() -> None:
    if not _truthy(os.getenv("MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED"), True):
        return
    try:
        from app.services import model
    except Exception:
        return
    cls = getattr(model, "CandidateFactory", None)
    if cls is None or getattr(cls, "_harizon_market_integrity_candidate_patch", False):
        return
    original = getattr(cls, "build_candidates", None)
    if not callable(original):
        return

    def build_candidates_patched(self, *args: Any, **kwargs: Any):
        candidates, rejections, debug = original(self, *args, **kwargs)
        if _truthy(os.getenv("MARKET_INTEGRITY_HARD_GUARD_ENABLED"), True):
            candidates = filter_candidates(list(candidates or []), rejections)
            if isinstance(debug, dict):
                debug["market_integrity_guard"] = {
                    "enabled": True,
                    "remaining_candidates": len(candidates),
                    "rejection_keys": {k: v for k, v in sorted((rejections or {}).items()) if str(k).startswith("market_integrity_")},
                }
        return candidates, rejections, debug

    cls.build_candidates = build_candidates_patched
    cls._harizon_market_integrity_candidate_patch = True


def install() -> None:
    os.environ.setdefault("MARKET_INTEGRITY_HARD_GUARD_ENABLED", "true")
    os.environ.setdefault("MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED", "true")
    os.environ.setdefault("MARKET_INTEGRITY_MIN_BOOKS", "2")
    os.environ.setdefault("MARKET_INTEGRITY_MIN_SOURCES", "1")
    os.environ.setdefault("MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS", "3")
    os.environ.setdefault("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS", "1.65")
    os.environ.setdefault("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS", "3")
    os.environ.setdefault("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS", "2.05")
    os.environ.setdefault("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT", "30")
    os.environ.setdefault("MARKET_INTEGRITY_MAX_EXACT_PRICE_DISPERSION_PCT", "22")
    os.environ.setdefault("MARKET_INTEGRITY_MAX_EXACT_LINE_DELTA_PCT", "18")
    os.environ.setdefault("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES", "true")
    os.environ.setdefault("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    install_candidate_factory_patch()
