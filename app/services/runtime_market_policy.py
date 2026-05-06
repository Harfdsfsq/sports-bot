from __future__ import annotations

"""Runtime market policy hooks.

Current production policy:
- valid selected picks must be publishable; Telegram must not block only because
  there is one odds-source when the quality/fallback gate selected the pick;
- duplicate control stays in publish_controlled_fallback/state indices;
- Asian quarter match-total lines (.25/.75) are valid when the whole market line
  has enough bookmaker support. Settlement already supports half_won/half_lost;
- market-derived candidates may use a same-run consensus relief path only when
  there is real bookmaker depth, low dispersion and a visible edge.
"""

import builtins
import math
import os
from collections import defaultdict
from typing import Any


_PATCH_MARKER = "_harizon_runtime_market_policy_v3"
_LEGACY_PATCH_MARKER = "_harizon_runtime_market_policy_v1"
_PREVIOUS_PATCH_MARKER = "_harizon_runtime_market_policy_v2"


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _family_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "")


def _norm_book(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def is_quarter_total_line(point: Any) -> bool:
    try:
        value = abs(float(point))
    except Exception:
        return False
    frac = value - math.floor(value)
    return abs(frac - 0.25) <= 1e-6 or abs(frac - 0.75) <= 1e-6


def quarter_totals_enabled() -> bool:
    return _truthy("ENABLE_QUARTER_TOTAL_LINES", True)


def quarter_total_min_books() -> int:
    return max(2, _env_int("QUARTER_TOTAL_MIN_BOOKS", 2))


def quarter_total_line_level_confirmation_enabled() -> bool:
    return _truthy("QUARTER_TOTAL_LINE_LEVEL_CONFIRMATION_ENABLED", True)


def _book_set(offers: list[Any]) -> set[str]:
    return {
        _norm_book(getattr(item, "bookmaker", None))
        for item in offers
        if str(getattr(item, "bookmaker", "") or "").strip()
    }


def _source_set(offers: list[Any]) -> set[str]:
    return {
        str(getattr(item, "source", "") or "").strip().lower()
        for item in offers
        if str(getattr(item, "source", "") or "").strip()
    }


def _filter_quarter_total_offer_buckets(offers: list[Any], rejections: dict[str, int] | None) -> list[Any]:
    """Keep Asian quarter totals only when the market line has support.

    Old behavior required 2+ books on the exact Over/Under selection bucket.
    odds feeds often split liquidity by direction, so valid 2.25/2.75 markets
    were being removed before the model could evaluate them. This gate now uses
    line-level support first, then lets later EV/quality/publish guards decide.
    """
    raw_offers = list(offers or [])
    if not raw_offers:
        return []

    by_point: dict[float | None, list[Any]] = defaultdict(list)
    exact_buckets: dict[tuple[str, float | None], list[Any]] = defaultdict(list)
    passthrough: list[Any] = []
    for offer in raw_offers:
        point = getattr(offer, "point", None)
        selection = str(getattr(offer, "selection", "") or "").strip().lower()
        if not is_quarter_total_line(point):
            passthrough.append(offer)
            continue
        try:
            normalized_point = float(point)
        except Exception:
            normalized_point = None
        by_point[normalized_point].append(offer)
        exact_buckets[(selection, normalized_point)].append(offer)

    kept = list(passthrough)
    allowed_ids: set[int] = set()
    for point, point_bucket in by_point.items():
        line_books = _book_set(point_bucket)
        line_sources = _source_set(point_bucket)
        if not quarter_totals_enabled():
            if isinstance(rejections, dict):
                rejections["quarter_total_line_removed"] = int(rejections.get("quarter_total_line_removed", 0) or 0) + len(point_bucket)
            continue
        if len(line_books) < quarter_total_min_books():
            if isinstance(rejections, dict):
                rejections["quarter_total_insufficient_books"] = int(rejections.get("quarter_total_insufficient_books", 0) or 0) + len(point_bucket)
            continue
        for (_selection, exact_point), exact_bucket in exact_buckets.items():
            if exact_point != point:
                continue
            exact_books = _book_set(exact_bucket)
            if len(exact_books) >= quarter_total_min_books() or quarter_total_line_level_confirmation_enabled():
                for item in exact_bucket:
                    allowed_ids.add(id(item))
                if isinstance(rejections, dict):
                    rejections["quarter_total_line_allowed"] = int(rejections.get("quarter_total_line_allowed", 0) or 0) + len(exact_bucket)
                    if len(exact_books) < quarter_total_min_books():
                        rejections["quarter_total_line_level_supported"] = int(rejections.get("quarter_total_line_level_supported", 0) or 0) + len(exact_bucket)
                        rejections["quarter_total_line_support_books"] = max(
                            int(rejections.get("quarter_total_line_support_books", 0) or 0),
                            len(line_books),
                        )
                        rejections["quarter_total_line_support_sources"] = max(
                            int(rejections.get("quarter_total_line_support_sources", 0) or 0),
                            len(line_sources),
                        )
            else:
                if isinstance(rejections, dict):
                    rejections["quarter_total_insufficient_books"] = int(rejections.get("quarter_total_insufficient_books", 0) or 0) + len(exact_bucket)
    kept.extend([item for item in raw_offers if id(item) in allowed_ids])
    return kept


def _extract_rejections(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, int] | None:
    if args and isinstance(args[-1], dict):
        return args[-1]
    value = kwargs.get("rejections")
    return value if isinstance(value, dict) else None


def _same_run_market_consensus_ready(self: Any, family: str, market_signal: dict[str, Any] | None, offers: list[Any] | None) -> bool:
    if not _truthy("MARKET_DERIVED_SAME_RUN_CONSENSUS_ENABLED", True):
        return False
    if not isinstance(market_signal, dict):
        return False
    family_key = _family_key(family)
    if family_key in {"spreads", "spread", "handicap"} and not _truthy("MARKET_DERIVED_SAME_RUN_SPREADS_ENABLED", False):
        return False
    if family_key in {"h2h", "moneyline"} and str(market_signal.get("selection_key") or "").strip().lower() == "draw":
        return False
    books_count = int(market_signal.get("books_count") or 0)
    sources_count = int(market_signal.get("sources_count") or 0)
    observation_count = int(market_signal.get("observation_count") or 0)
    if offers is not None:
        books_count = max(books_count, len(_book_set(list(offers or []))))
        sources_count = max(sources_count, len(_source_set(list(offers or []))))
    min_books = max(2, _env_int("MARKET_DERIVED_SAME_RUN_MIN_BOOKS", 2))
    min_sources = max(1, _env_int("MARKET_DERIVED_SAME_RUN_MIN_SOURCES", 1))
    min_observations = max(1, _env_int("MARKET_DERIVED_SAME_RUN_MIN_OBSERVATIONS", 1))
    if books_count < min_books or sources_count < min_sources or observation_count < min_observations:
        return False
    edge_pct = _to_float(market_signal.get("best_vs_consensus_edge_pct"), 0.0)
    min_edge = _env_float("MARKET_DERIVED_SAME_RUN_MIN_EDGE_PCT", 2.4)
    if family_key in {"totals", "total", "matchtotal"}:
        min_edge = _env_float("MARKET_DERIVED_SAME_RUN_TOTALS_MIN_EDGE_PCT", min_edge)
    elif family_key in {"h2h", "moneyline"}:
        min_edge = _env_float("MARKET_DERIVED_SAME_RUN_H2H_MIN_EDGE_PCT", max(2.8, min_edge))
    if edge_pct < min_edge:
        return False
    dispersion = market_signal.get("consensus_dispersion_pct")
    if dispersion not in (None, ""):
        max_dispersion = _env_float("MARKET_DERIVED_SAME_RUN_MAX_DISPERSION_PCT", 4.8)
        if _to_float(dispersion, 999.0) > max_dispersion:
            return False
    return True


def _install_candidate_factory_policy() -> None:
    try:
        import app.services.model as model_module
    except Exception:
        return
    cls = getattr(model_module, "CandidateFactory", None)
    if cls is None or getattr(cls, _PATCH_MARKER, False):
        return

    original_normalize = getattr(cls, "_normalize_supported_line", None)
    if callable(original_normalize):
        def normalize_supported_line_patched(self, point, family):
            if _family_key(family) in {"totals", "total", "matchtotal"} and is_quarter_total_line(point):
                return float(point) if quarter_totals_enabled() else None
            return original_normalize(self, point, family)

        cls._normalize_supported_line = normalize_supported_line_patched

    original_required = getattr(cls, "_required_books_for_bucket", None)
    if callable(original_required):
        def required_books_for_bucket_patched(self, family, point, bucket, context=None):
            required = original_required(self, family, point, bucket, context)
            if _family_key(family) in {"totals", "total", "matchtotal"} and is_quarter_total_line(point):
                if quarter_total_line_level_confirmation_enabled() and _filter_quarter_total_offer_buckets(list(bucket or []), None):
                    return min(int(required or 1), 1)
            return required

        cls._required_books_for_bucket = required_books_for_bucket_patched

    original_ready = getattr(cls, "_market_signal_ready_for_derived", None)
    if callable(original_ready):
        def market_signal_ready_for_derived_patched(self, family, market_signal, offers=None):
            if original_ready(self, family, market_signal, offers):
                return True
            return _same_run_market_consensus_ready(self, str(family or ""), market_signal, offers)

        cls._market_signal_ready_for_derived = market_signal_ready_for_derived_patched

    original_filter = getattr(cls, "_filter_and_rank", None)
    if callable(original_filter):
        def filter_and_rank_patched(self, candidates, rejections):
            if isinstance(rejections, dict):
                rejections["internal_candidates_before_filter"] = int(rejections.get("internal_candidates_before_filter", 0) or 0) + len(list(candidates or []))
            result = original_filter(self, candidates, rejections)
            if isinstance(rejections, dict):
                rejections["internal_candidates_after_filter"] = int(rejections.get("internal_candidates_after_filter", 0) or 0) + len(list(result or []))
            return result

        cls._filter_and_rank = filter_and_rank_patched

    # Gate .25/.75 total lines by market-line support instead of dropping them all.
    for method_name in (
        "_build_totals_candidates",
        "_build_market_derived_totals_candidates",
        "_build_simple_market_totals_candidates",
    ):
        original = getattr(cls, method_name, None)
        if not callable(original):
            continue

        def make_wrapper(fn):
            def wrapper(self, match, offers, *args, **kwargs):
                rejections = _extract_rejections(args, kwargs)
                filtered = _filter_quarter_total_offer_buckets(list(offers or []), rejections)
                if not filtered:
                    return []
                return fn(self, match, filtered, *args, **kwargs)
            return wrapper

        setattr(cls, method_name, make_wrapper(original))

    setattr(cls, _PATCH_MARKER, True)
    setattr(cls, _PREVIOUS_PATCH_MARKER, True)
    # Keep the legacy marker too, so old sitecustomize/bootstrap checks do not
    # install the previous "remove all quarter totals" behavior again.
    setattr(cls, _LEGACY_PATCH_MARKER, True)


def install() -> None:
    """Install market policy hooks now and on future imports."""
    if getattr(builtins, _PATCH_MARKER, False):
        _install_candidate_factory_policy()
        return

    original_import = builtins.__import__

    def import_patched(name, globals=None, locals=None, fromlist=(), level=0):
        module = original_import(name, globals, locals, fromlist, level)
        if name == "app.services.model" or name.startswith("app.services.model"):
            _install_candidate_factory_policy()
        return module

    builtins.__import__ = import_patched
    setattr(builtins, _PATCH_MARKER, True)
    setattr(builtins, _PREVIOUS_PATCH_MARKER, True)
    # Mark the old patch as installed at package level as well. This prevents
    # older runtime layers from reinstalling the destructive filter.
    setattr(builtins, _LEGACY_PATCH_MARKER, True)
    _install_candidate_factory_policy()


# Allow direct import side effect from sitecustomize/service package startup.
if os.getenv("HARIZON_RUNTIME_MARKET_POLICY_AUTOINSTALL", "true").strip().lower() in {"1", "true", "yes", "on", "force"}:
    install()
