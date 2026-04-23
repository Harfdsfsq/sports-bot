from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


@dataclass(slots=True)
class CandidateIntegrityReport:
    suspicious: bool
    reasons: list[str]
    selected_odds: float | None = None
    selected_implied_probability: float | None = None
    market_probability: float | None = None
    fair_odds_from_market: float | None = None
    adjusted_probability: float | None = None
    final_probability: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_candidate_integrity(candidate: Any, *, implied_tolerance_pp: float = 2.0, adjusted_tolerance_pp: float = 2.0) -> CandidateIntegrityReport:
    reasons: list[str] = []

    odds = _to_float(getattr(candidate, "odds", None))
    implied = _to_float(getattr(candidate, "implied_probability", None))
    market_probability = _to_float(getattr(candidate, "market_probability", None))
    fair_odds = _to_float(getattr(candidate, "fair_odds", None))
    adjusted = _to_float(getattr(candidate, "adjusted_probability", None))
    final_probability = _to_float(getattr(candidate, "final_probability", None))
    edge_pct = _to_float(getattr(candidate, "edge_pct", None))
    ev_pct = _to_float(getattr(candidate, "ev_pct", None))

    source_summary = getattr(candidate, "source_summary", None) or {}
    source_adjusted = _to_float(source_summary.get("adjusted_probability"))
    selected_price = _to_float(source_summary.get("selected_price"))

    if odds and implied:
        expected = 1.0 / odds
        mismatch_pp = abs(expected - implied) * 100.0
        if mismatch_pp > implied_tolerance_pp:
            reasons.append(f"implied_mismatch:{mismatch_pp:.2f}pp")

    if odds is not None and selected_price is not None and abs(odds - selected_price) > 1e-6:
        reasons.append(f"selected_price_mismatch:{abs(odds - selected_price):.4f}")

    if adjusted is not None and source_adjusted is not None:
        mismatch_pp = abs(adjusted - source_adjusted) * 100.0
        if mismatch_pp > adjusted_tolerance_pp:
            reasons.append(f"adjusted_mismatch:{mismatch_pp:.2f}pp")

    if adjusted is not None and final_probability is not None:
        mismatch_pp = abs(adjusted - final_probability) * 100.0
        if mismatch_pp > adjusted_tolerance_pp:
            reasons.append(f"final_probability_mismatch:{mismatch_pp:.2f}pp")

    if market_probability and fair_odds:
        expected_fair = 1.0 / market_probability
        if abs(expected_fair - fair_odds) > 0.05:
            reasons.append(f"fair_odds_mismatch:{abs(expected_fair - fair_odds):.4f}")

    if edge_pct is not None and ev_pct is not None and edge_pct < 0 and ev_pct > 0:
        reasons.append("negative_edge_positive_ev_conflict")

    return CandidateIntegrityReport(
        suspicious=bool(reasons),
        reasons=reasons,
        selected_odds=odds,
        selected_implied_probability=(1.0 / odds if odds else None),
        market_probability=market_probability,
        fair_odds_from_market=(1.0 / market_probability if market_probability else None),
        adjusted_probability=adjusted,
        final_probability=final_probability,
    )
