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


def _offer_dicts(candidate: Any) -> list[dict[str, Any]]:
    raw = getattr(candidate, "raw_bucket_offers", None)
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, dict)]
    summary = getattr(candidate, "source_summary", None) or {}
    for key in ("offers", "bucket_offers", "selected_offers"):
        value = summary.get(key) if isinstance(summary, dict) else None
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
    for offer in _offer_dicts(candidate)[:8]:
        for key in ("family", "market_name", "market_key", "market_subtype", "selection"):
            parts.append(str(offer.get(key) or ""))
    return " ".join(parts).lower()


def _sources_count(candidate: Any) -> int:
    value = _int(getattr(candidate, "sources_count", 0), 0)
    offers = _offer_dicts(candidate)
    if offers:
        value = max(value, len({str(x.get("source") or "").strip().lower() for x in offers if str(x.get("source") or "").strip()}))
    return value


def _books_count(candidate: Any) -> int:
    value = _int(getattr(candidate, "books_count", 0), 0)
    offers = _offer_dicts(candidate)
    if offers:
        value = max(value, len({str(x.get("bookmaker") or "").strip().lower() for x in offers if str(x.get("bookmaker") or "").strip()}))
    return value


def _best_price(candidate: Any) -> float:
    prices: list[float] = []
    for attr in ("odds", "selected_odds", "price_used_for_ev"):
        value = _float(getattr(candidate, attr, None))
        if value and value > 1.0:
            prices.append(value)
    for offer in _offer_dicts(candidate):
        value = _float(offer.get("price") or offer.get("odds"))
        if value and value > 1.0:
            prices.append(value)
    return max(prices) if prices else 0.0


def _price_dispersion(candidate: Any) -> float | None:
    prices = []
    for offer in _offer_dicts(candidate):
        value = _float(offer.get("price") or offer.get("odds"))
        if value and value > 1.0:
            prices.append(value)
    if len(prices) < 2:
        return None
    med = median(prices)
    if med <= 0:
        return None
    return max(abs(p - med) / med for p in prices) * 100.0


def validate_candidate(candidate: Any) -> IntegrityDecision:
    reasons: list[str] = []
    family = str(getattr(candidate, "family", "") or "").strip()
    family_low = family.lower()
    point = _float(getattr(candidate, "point", None))
    price = _best_price(candidate)
    books = _books_count(candidate)
    sources = _sources_count(candidate)
    text = _market_text(candidate)
    dispersion = _price_dispersion(candidate)

    min_books = _int(os.getenv("MARKET_INTEGRITY_MIN_BOOKS"), 2)
    min_sources = _int(os.getenv("MARKET_INTEGRITY_MIN_SOURCES"), 1)
    strict_single_source_books = _int(os.getenv("MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS"), 3)

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
        if point is not None and point <= 1.5 and price > max_over15 and not (sources >= 2 and books >= 2):
            reasons.append(f"suspicious_low_total_price:point={point:g},odds={price:.2f},books={books},sources={sources}")
        max_over20 = _float(os.getenv("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS"), 2.05) or 2.05
        if point is not None and point <= 2.0 and price > max_over20 and not (sources >= 2 and books >= 2):
            reasons.append(f"suspicious_total_2_price:point={point:g},odds={price:.2f},books={books},sources={sources}")

    if family_low == "teamtotals" and not _truthy(os.getenv("TEAM_TOTALS_PUBLICATION_ENABLED"), False):
        reasons.append("team_totals_quarantined")

    if family_low == "spreads" and _truthy(os.getenv("DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED"), True):
        reasons.append("spreads_quarantined")

    if dispersion is not None:
        max_disp = _float(os.getenv("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT"), 30.0) or 30.0
        if dispersion > max_disp and sources < 2:
            reasons.append(f"single_source_outlier_dispersion:{dispersion:.1f}%")

    report = {
        "family": family,
        "point": point,
        "price": price,
        "books_count": books,
        "sources_count": sources,
        "price_dispersion_pct": round(dispersion, 3) if dispersion is not None else None,
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
    os.environ.setdefault("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS", "2.05")
    os.environ.setdefault("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT", "30")
    install_candidate_factory_patch()
