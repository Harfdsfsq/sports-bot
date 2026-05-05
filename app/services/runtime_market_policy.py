from __future__ import annotations

"""Runtime market policy hooks.

Current production policy:
- valid selected picks must be publishable; Telegram must not block only because
  there is one odds-source when the quality/fallback gate selected the pick;
- duplicate control stays in publish_controlled_fallback/state indices;
- Asian quarter match-total lines (.25/.75) are valid only when enough bookmaker
  support exists. They are no longer removed globally because settlement already
  supports half_won/half_lost grading.
"""

import builtins
import math
import os
import sys
from collections import defaultdict
from typing import Any


_PATCH_MARKER = "_harizon_runtime_market_policy_v2"
_LEGACY_PATCH_MARKER = "_harizon_runtime_market_policy_v1"


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


def _family_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "")


def _norm_book(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


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


def _filter_quarter_total_offer_buckets(offers: list[Any], rejections: dict[str, int] | None) -> list[Any]:
    """Keep Asian quarter totals only when a real cross-book bucket exists.

    This avoids two bad outcomes:
    1) all .25/.75 total markets being thrown away before candidate generation;
    2) one-book quarter totals creating weak market-derived candidates.
    """
    raw_offers = list(offers or [])
    if not raw_offers:
        return []

    buckets: dict[tuple[str, float | None], list[Any]] = defaultdict(list)
    passthrough: list[Any] = []
    for offer in raw_offers:
        point = getattr(offer, "point", None)
        selection = str(getattr(offer, "selection", "") or "").strip()
        if not is_quarter_total_line(point):
            passthrough.append(offer)
            continue
        try:
            normalized_point = float(point)
        except Exception:
            normalized_point = None
        buckets[(selection.lower(), normalized_point)].append(offer)

    kept = list(passthrough)
    for _key, bucket in buckets.items():
        books = {
            _norm_book(getattr(item, "bookmaker", None))
            for item in bucket
            if str(getattr(item, "bookmaker", "") or "").strip()
        }
        if not quarter_totals_enabled():
            if isinstance(rejections, dict):
                rejections["quarter_total_line_removed"] = int(rejections.get("quarter_total_line_removed", 0) or 0) + len(bucket)
            continue
        if len(books) < quarter_total_min_books():
            if isinstance(rejections, dict):
                rejections["quarter_total_insufficient_books"] = int(rejections.get("quarter_total_insufficient_books", 0) or 0) + len(bucket)
            continue
        kept.extend(bucket)
        if isinstance(rejections, dict):
            rejections["quarter_total_line_allowed"] = int(rejections.get("quarter_total_line_allowed", 0) or 0) + len(bucket)
    return kept


def _extract_rejections(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, int] | None:
    if args and isinstance(args[-1], dict):
        return args[-1]
    value = kwargs.get("rejections")
    return value if isinstance(value, dict) else None


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

    # Gate .25/.75 total lines by bookmaker support instead of dropping them all.
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
    # Mark the old patch as installed at package level as well. This prevents
    # older runtime layers from reinstalling the destructive filter.
    setattr(builtins, _LEGACY_PATCH_MARKER, True)
    _install_candidate_factory_policy()


# Allow direct import side effect from sitecustomize/service package startup.
if os.getenv("HARIZON_RUNTIME_MARKET_POLICY_AUTOINSTALL", "true").strip().lower() in {"1", "true", "yes", "on", "force"}:
    install()
