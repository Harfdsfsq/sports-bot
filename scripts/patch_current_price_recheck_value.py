from __future__ import annotations

"""Current-price EV recheck for controlled fallback.

Does not disable price integrity. When selected price is stale, classify whether
value is still alive at the current price. If still valid for B-tier, remove only
the stale selected-price reason and let normal movement/dedupe/xG/odds guards
continue to decide.
"""

import re
from typing import Any


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def _prob_from_ev(price: float, ev_pct: float) -> float:
    if price <= 1.0:
        return 0.0
    return max(0.0, min(0.98, (1.0 + ev_pct / 100.0) / price))


def _selected_current(reason: str) -> tuple[float, float] | None:
    m = re.search(r"semantic_selected_price_not_current:([0-9.]+)/([0-9.]+)", str(reason))
    if not m:
        return None
    return _num(m.group(1)), _num(m.group(2))


def install(base: Any) -> dict[str, Any]:
    old = getattr(base, "hard_reject_reasons", None)
    if not callable(old) or getattr(base, "_harizon_current_price_recheck", False):
        return {"status": "already_installed" if getattr(base, "_harizon_current_price_recheck", False) else "missing_hard_reject"}

    def hard_reject(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(old(candidate, metrics, sent_index) or [])
        ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")), _num(candidate.get("ev_pct")))
        edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")), _num(candidate.get("edge_pp")))
        new_reasons: list[str] = []
        rechecked = False
        for reason in reasons:
            pair = _selected_current(str(reason))
            if not pair:
                new_reasons.append(reason)
                continue
            selected, current = pair
            prob = _prob_from_ev(selected, ev)
            current_ev = (prob * current - 1.0) * 100.0 if current > 1.0 else -999.0
            current_edge = (prob - 1.0 / current) * 100.0 if current > 1.0 else -999.0
            metrics["current_price_recheck"] = {
                "selected_price": round(selected, 4),
                "current_price": round(current, 4),
                "model_probability_from_selected_ev": round(prob, 5),
                "recalculated_ev_pct": round(current_ev, 2),
                "recalculated_edge_pp": round(current_edge, 2),
            }
            # Keep price-integrity strict: only recover when current quote still
            # clears the B-tier value floor and the drift is not extreme.
            drift_pct = abs(selected - current) / max(current, 1e-9) * 100.0
            if current_ev >= 4.0 and current_edge >= 2.3 and drift_pct <= 8.0:
                metrics["current_price_recheck"]["status"] = "value_still_valid_at_current_price"
                rechecked = True
                continue
            metrics["current_price_recheck"]["status"] = "value_lost_after_current_price_recheck"
            new_reasons.append(f"current_price_recheck_value_lost:{current_ev:.1f}/{current_edge:.1f}")
        if rechecked:
            metrics.setdefault("repaired_reasons", []).append("semantic_selected_price_not_current_rechecked_value_alive")
        return list(dict.fromkeys(new_reasons))

    base.hard_reject_reasons = hard_reject
    base._harizon_current_price_recheck = True
    return {"status": "installed", "publication_contract_relaxed": False}


__all__ = ["install"]
