from __future__ import annotations

import math
import re
from typing import Any

_PATCHED = False


def _extract_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    text = str(value).strip().replace("\xa0", " ").replace(",", ".").replace("−", "-")
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _extract_probability(value: Any) -> float | None:
    number = _extract_number(value)
    if number is None:
        return None
    if isinstance(value, str) and "%" in value:
        number /= 100.0
    elif number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _needs_reject(factory: Any, item: Any) -> str | None:
    family = str(getattr(item, "family", "") or "").strip().lower()
    if family not in {"h2h", "spreads", "dnb"}:
        return None

    books_count = int(getattr(item, "books_count", 0) or 0)
    sources_count = int(getattr(item, "sources_count", 0) or 0)
    confidence = float(getattr(item, "confidence", 0.0) or 0.0)
    publication_score = float(getattr(item, "publication_score", 0.0) or 0.0)
    edge_pct = float(getattr(item, "edge_pct", 0.0) or 0.0)
    ev_pct = float(getattr(item, "ev_pct", 0.0) or 0.0)
    odds = float(getattr(item, "odds", 0.0) or 0.0)
    source_summary = dict(getattr(item, "source_summary", {}) or {})
    context_source = str(source_summary.get("context_source") or "").strip().lower()
    raw_model = _extract_probability(source_summary.get("raw_model_probability"))
    adjusted = _extract_probability(source_summary.get("adjusted_probability") or getattr(item, "adjusted_probability", None))
    market = _extract_probability(source_summary.get("market_probability") or getattr(item, "market_probability", None))
    shrink_drop_pp = ((raw_model - adjusted) * 100.0) if raw_model is not None and adjusted is not None else 0.0
    probability_gap_pp = ((adjusted - market) * 100.0) if adjusted is not None and market is not None else 0.0
    has_core_context = bool(getattr(factory, "_has_core_context")(item))

    weak_context_source = context_source in {"", "market_signal", "newsapi", "gnews"}

    if family in {"spreads", "dnb"} and sources_count <= 1:
        if shrink_drop_pp >= 10.0 and (confidence < 70.0 or publication_score < 58.0 or edge_pct < 6.0):
            return "runtime_single_source_heavy_shrink_spread_guard"
        if weak_context_source and publication_score < 60.0:
            return "runtime_single_source_weak_context_spread_guard"
        if not has_core_context and (confidence < 69.0 or ev_pct < 3.5):
            return "runtime_single_source_noncore_context_spread_guard"
        if books_count <= 2 and probability_gap_pp < 5.0 and publication_score < 62.0:
            return "runtime_single_source_low_gap_spread_guard"

    if family == "h2h" and sources_count <= 1:
        if odds >= 3.4 and (shrink_drop_pp >= 9.0 or not has_core_context):
            return "runtime_single_source_high_odds_h2h_guard"
        if books_count <= 2 and probability_gap_pp < 5.0 and publication_score < 60.0:
            return "runtime_single_source_low_gap_h2h_guard"

    return None


def apply_runtime_fixes() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from app.providers.api_football import ApiFootballContextProvider
    from app.services.model import CandidateFactory

    # --- Numeric parsing hardening for api-football and mixed % fields ---
    ApiFootballContextProvider._to_float = staticmethod(_extract_number)

    CandidateFactory._to_float_safe = staticmethod(_extract_number)

    def _patched_metric01(value: Any) -> float | None:
        number = _extract_probability(value)
        if number is None:
            return None
        return max(0.0, min(1.0, number))

    def _patched_safe_probability(value: Any) -> float | None:
        number = _extract_probability(value)
        if number is None:
            return None
        return max(0.01, min(0.95, number))

    def _patched_first_float(details: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            if key not in details:
                continue
            number = _extract_number(details.get(key))
            if number is not None:
                return number
        return None

    CandidateFactory._metric01 = staticmethod(_patched_metric01)
    CandidateFactory._safe_probability = staticmethod(_patched_safe_probability)
    CandidateFactory._first_float = staticmethod(_patched_first_float)

    # --- Post-filter publication guard to stop weak single-source heavy-shrink picks ---
    original_filter_and_rank = CandidateFactory._filter_and_rank

    def _patched_filter_and_rank(self: Any, candidates: list[Any], rejections: dict[str, int]) -> list[Any]:
        ranked = list(original_filter_and_rank(self, candidates, rejections))
        if not ranked:
            return ranked
        cleaned: list[Any] = []
        for item in ranked:
            reason = _needs_reject(self, item)
            if reason:
                rejections[reason] += 1
                continue
            cleaned.append(item)
        return cleaned

    CandidateFactory._filter_and_rank = _patched_filter_and_rank
    _PATCHED = True
