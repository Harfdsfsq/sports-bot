from __future__ import annotations

"""Runtime market policy hooks.

Current production policy:
- valid selected picks must be publishable; Telegram must not block only because
  there is one odds-source when the quality/fallback gate selected the pick;
- duplicate control stays in publish_controlled_fallback/state indices;
- quarter totals (.25/.75) are removed before candidate construction so the bot
  does not analyze or publish Asian quarter total lines.
"""

import builtins
import math
import os
import sys
from typing import Any


_PATCH_MARKER = "_harizon_runtime_market_policy_v1"


def _family_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "")


def is_quarter_total_line(point: Any) -> bool:
    try:
        value = abs(float(point))
    except Exception:
        return False
    frac = value - math.floor(value)
    return abs(frac - 0.25) <= 1e-6 or abs(frac - 0.75) <= 1e-6


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
                # Returning None reuses existing unsupported-line rejection paths.
                return None
            return original_normalize(self, point, family)

        cls._normalize_supported_line = normalize_supported_line_patched

    # Extra pre-filter: even if another path bypasses _normalize_supported_line,
    # totals with .25/.75 never reach candidate builders.
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
                filtered = [offer for offer in list(offers or []) if not is_quarter_total_line(getattr(offer, "point", None))]
                dropped = len(list(offers or [])) - len(filtered)
                if dropped > 0:
                    try:
                        rejections = args[-1] if args and isinstance(args[-1], dict) else kwargs.get("rejections")
                        if isinstance(rejections, dict):
                            rejections["quarter_total_line_removed"] = int(rejections.get("quarter_total_line_removed", 0) or 0) + dropped
                    except Exception:
                        pass
                if not filtered:
                    return []
                return fn(self, match, filtered, *args, **kwargs)
            return wrapper

        setattr(cls, method_name, make_wrapper(original))

    setattr(cls, _PATCH_MARKER, True)


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
    _install_candidate_factory_policy()


# Allow direct import side effect from sitecustomize.
if os.getenv("HARIZON_RUNTIME_MARKET_POLICY_AUTOINSTALL", "true").strip().lower() in {"1", "true", "yes", "on", "force"}:
    install()
