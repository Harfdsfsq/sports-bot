from __future__ import annotations

"""Patch fallback xG direction sanity with probability support.

The base guard compares total xG directly with the public total line.  That is a
useful warning, but for totals such as Under 1.5 a total xG slightly above the
line can still imply a selected-outcome probability above market implied odds.
If xG probability supports the selected price and the model is not more
optimistic than xG, the candidate should not be rejected as an xG-direction
conflict.  All value, line movement, 2-book and price-integrity guards remain in
place.
"""

import os
from typing import Any


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _bool_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def install(base: Any) -> None:
    if getattr(base, "_harizon_xg_probability_support_patch", False):
        return
    original = getattr(base, "xg_sanity_metrics", None)
    if not callable(original):
        return

    def patched(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
        metrics = dict(original(candidate, adjusted_probability) or {})
        if not _bool_env("CONTROLLED_FALLBACK_XG_PROBABILITY_SUPPORT_PATCH_ENABLED", True):
            return metrics
        if not metrics.get("enabled") or bool(metrics.get("xg_direction_ok", True)):
            return metrics
        fam = str(candidate.get("family") or candidate.get("market_family") or "").strip().lower()
        if fam not in {"totals", "teamtotals"}:
            return metrics
        odds = _float(candidate.get("odds") or candidate.get("selected_odds"), 0.0)
        if odds <= 1.0:
            return metrics
        xg_probability = _float(metrics.get("xg_probability"), 0.0)
        adjusted = _float(adjusted_probability, 0.0)
        implied = 1.0 / odds
        min_support_edge_pp = _float(os.getenv("CONTROLLED_FALLBACK_XG_PROB_SUPPORT_MIN_EDGE_PP"), 1.0)
        max_model_over_xg_pp = _float(os.getenv("CONTROLLED_FALLBACK_XG_PROB_SUPPORT_MAX_MODEL_OVER_XG_PP"), 0.75)
        supports_price = (xg_probability - implied) * 100.0 >= min_support_edge_pp
        model_not_over_xg = (adjusted - xg_probability) * 100.0 <= max_model_over_xg_pp
        if supports_price and model_not_over_xg:
            metrics["xg_direction_ok"] = True
            metrics["xg_direction_probability_support_override"] = {
                "used": True,
                "xg_probability_pct": round(xg_probability * 100.0, 3),
                "implied_probability_pct": round(implied * 100.0, 3),
                "adjusted_probability_pct": round(adjusted * 100.0, 3),
                "min_support_edge_pp": min_support_edge_pp,
                "max_model_over_xg_pp": max_model_over_xg_pp,
                "reason": "xg_probability_supports_selected_price_and_model_not_over_xg",
            }
        return metrics

    base.xg_sanity_metrics = patched
    base._harizon_xg_probability_support_patch = True


__all__ = ["install"]
