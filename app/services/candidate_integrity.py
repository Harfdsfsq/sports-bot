from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _clamp_probability(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


@dataclass(slots=True)
class CandidateIntegrityResult:
    ok: bool
    suspicious: bool
    blocking: bool
    issues: list[str]
    odds: float | None
    selected_price: float | None
    selected_implied_probability: float | None
    recorded_implied_probability: float | None
    market_probability: float | None
    fair_odds: float | None
    fair_odds_from_market: float | None
    adjusted_probability: float | None
    source_summary_adjusted_probability: float | None
    final_probability: float | None
    edge_pct: float | None
    ev_pct: float | None
    canonical_edge_pct: float | None
    canonical_ev_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_candidate_integrity(
    candidate: dict[str, Any],
    *,
    implied_tolerance: float = 0.035,
    adjusted_tolerance: float = 0.025,
    fair_odds_ratio_limit: float = 1.35,
    block_on_issue: bool = False,
) -> CandidateIntegrityResult:
    """Validate consistency of odds/probability fields without mutating the candidate.

    The current CandidateBet schema historically stores market probability in
    `implied_probability` in some paths. This checker makes that visible and
    computes canonical values from the selected odds so a publish layer can
    avoid sending dirty candidates.
    """

    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}

    odds = _to_float(candidate.get("odds"))
    selected_price = _to_float(source_summary.get("selected_price")) or odds
    selected_implied = (1.0 / selected_price) if selected_price and selected_price > 1.0 else None

    recorded_implied = _clamp_probability(_to_float(candidate.get("implied_probability")))
    market_probability = _clamp_probability(_to_float(candidate.get("market_probability")))
    fair_odds = _to_float(candidate.get("fair_odds"))
    fair_odds_from_market = (1.0 / market_probability) if market_probability and market_probability > 0 else None

    adjusted_probability = _clamp_probability(_to_float(candidate.get("adjusted_probability")))
    source_summary_adjusted = _clamp_probability(_to_float(source_summary.get("adjusted_probability")))
    final_probability = _clamp_probability(_to_float(candidate.get("final_probability")))

    edge_pct = _to_float(candidate.get("edge_pct"))
    ev_pct = _to_float(candidate.get("ev_pct"))

    probability_for_ev = final_probability or adjusted_probability
    market_for_edge = market_probability or recorded_implied
    canonical_edge_pct = None
    canonical_ev_pct = None
    if probability_for_ev is not None and market_for_edge is not None:
        canonical_edge_pct = (probability_for_ev - market_for_edge) * 100.0
    if probability_for_ev is not None and selected_price is not None:
        canonical_ev_pct = ((probability_for_ev * selected_price) - 1.0) * 100.0

    issues: list[str] = []

    if selected_price is None or selected_price <= 1.0:
        issues.append("missing_or_invalid_selected_price")

    if selected_implied is not None and recorded_implied is not None:
        gap = abs(selected_implied - recorded_implied)
        if gap > implied_tolerance:
            issues.append(f"implied_mismatch:{gap:.4f}")

    if odds is not None and selected_price is not None and abs(odds - selected_price) > 0.005:
        issues.append(f"odds_selected_price_mismatch:{abs(odds - selected_price):.4f}")

    if fair_odds is not None and fair_odds_from_market is not None:
        ratio = max(fair_odds, fair_odds_from_market) / max(0.0001, min(fair_odds, fair_odds_from_market))
        if ratio > fair_odds_ratio_limit:
            issues.append(f"fair_odds_market_mismatch:{ratio:.3f}")

    if adjusted_probability is not None and source_summary_adjusted is not None:
        gap = abs(adjusted_probability - source_summary_adjusted)
        if gap > adjusted_tolerance:
            issues.append(f"adjusted_mismatch:{gap:.4f}")

    if adjusted_probability is not None and final_probability is not None:
        gap = abs(adjusted_probability - final_probability)
        if gap > adjusted_tolerance:
            issues.append(f"final_adjusted_mismatch:{gap:.4f}")

    if edge_pct is not None and ev_pct is not None and edge_pct < -0.01 and ev_pct > 0.01:
        issues.append("negative_edge_positive_ev")

    if canonical_ev_pct is not None and ev_pct is not None and abs(canonical_ev_pct - ev_pct) > 3.0:
        issues.append(f"ev_canonical_mismatch:{abs(canonical_ev_pct - ev_pct):.2f}pp")

    if canonical_edge_pct is not None and edge_pct is not None and abs(canonical_edge_pct - edge_pct) > 3.0:
        issues.append(f"edge_canonical_mismatch:{abs(canonical_edge_pct - edge_pct):.2f}pp")

    suspicious = bool(issues)
    blocking = bool(block_on_issue and issues)
    return CandidateIntegrityResult(
        ok=not suspicious,
        suspicious=suspicious,
        blocking=blocking,
        issues=issues,
        odds=odds,
        selected_price=selected_price,
        selected_implied_probability=selected_implied,
        recorded_implied_probability=recorded_implied,
        market_probability=market_probability,
        fair_odds=fair_odds,
        fair_odds_from_market=fair_odds_from_market,
        adjusted_probability=adjusted_probability,
        source_summary_adjusted_probability=source_summary_adjusted,
        final_probability=final_probability,
        edge_pct=edge_pct,
        ev_pct=ev_pct,
        canonical_edge_pct=canonical_edge_pct,
        canonical_ev_pct=canonical_ev_pct,
    )
